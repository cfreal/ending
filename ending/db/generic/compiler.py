"""Converts an SQL AST into an SQL expression.

A compiler provides methods to convert each `ending.ast.Node` to an SQL query
element, in order to build DBMS-specific SQL injection payloads from an AST.
Additionally, it provides methods to serialize (convert to text) and deserialize
(convert back to original type) SQL results.

# Usage

After instanciating a compiler, one can wrap an AST node to bind it to the
compiler:

    >>> node = Query('users').columns('username', 'password')
    >>> compiler.wrap(node)
    >>> print(node)
    'SELECT username, password FROM users'

# DBMS-Specific compilers

The original `Compiler` class converts AST to the standard SQL syntax. Compiler
implementations for other DBMS must modify the convertion of some nodes into
specific syntax. For instance, PostgreSQL supports `LIMIT y OFFSET x` instead of
the standard `LIMIT x, y`, and therefore it implements a custom
`Compiler.compile_Query` method.

Each `ending.ast.Node` has a corresponding `compile_` method, which returns a
string representation of the node or another node, whose string representation
will then be computed.

# WAF bypass

In addition, one can easily build a custom `Compiler` in order to bypass WAF or
DBMS limitations. For instance, if you have an SQL injection, but the WAF blocks
the `LIKE` keyword, but now its lowercase equivalent, `like`.
`LIKE` is a node of type `WordComparison`, so we can edit the method which
handles its conversion to SQL syntax.

    class MyCompiler(Compiler):
        def compile_WordedComparison(self, comparison: WordedComparison, s) -> str:
            if comparison.operator == 'LIKE':
                comparison = WordedComparison(comparison.left, 'like', comparison.right)
            return super().compile_WordedComparison(comparison, s)

If in addition, the `<` character is blocked by the WAF, we can edit the
`Comparison` node:

        def compile_Comparison(self, comparison: Comparison, s) -> str:
            # Invert the comparison
            if c.operator == '<':
                return Comparison(comparison.right, '>', comparison.left)
            return super().compile_Comparison(comparison, s)

Changing the node and calling the super-method instead of returning a string
directly is safer, but if you're sure of what you're doing, you can return a
string directly.
"""

import functools
from itertools import chain
import typing
from abc import ABC, abstractmethod
from typing import Any, Callable

from ending import ast
from ending.ast import *
from ending.exception import ConversionError

__all__ = ["Compiler", "HasConcatWSMixin", "parenthesize"]


def parenthesize(function):
    """Decorator for compiler methods. If 'p' is given in the format
    specification, surrounds payload with parentheses. Some elements do not
    require parentheses. Values, for instance, are self contained.
    """

    @functools.wraps(function)
    def modifier_function(self, x, s):
        r = function(self, x, s)
        if "p" in s:
            r = f"({r})"
        return r

    return modifier_function


class Compiler(ABC):
    """A class that converts SQL nodes into SQL expressions.
    A method is defined to convert each `ending.ast.Node` type to text.

    The two properties of compilers are `quote`, which escapes and quotes a string, and
    `encoding`, which is the encoding of the database.
    """

    quote: Callable[[bytes], str]
    """Quotes a byte value.
    See `ending.util.quoting` for examples of quote functions.
    """
    encoding: str
    """The encoding of the database. Columns of type text will get decoded using this
    encoding. Defaults to `utf-8`.
    """

    def __init__(self, quote: Callable[[str], str], encoding: str = "utf-8"):
        self.quote = quote
        self.encoding = encoding
        self._compile_methods_cache: dict[type[Node], Callable] = {}
        """Caching the compilation method for each node type provides slight performance
        gains, which are always good to take.
        """

    def compile(self, node: Node, s: str = "") -> str:
        """Converts a `Node` into SQL syntax.

        Args:
            node (Node): AST node to build
            s (str): format specifier

        Returns:
            str: SQL syntax equivalent
        """
        try:
            if (cached := node.__compiled__.get(s)) is not None:
                return cached
        except AttributeError:
            raise TypeError(f"Expected Node, got {type(node).__name__}")

        self.wrap(node)

        # Get compilation method from the cache, or derive it from the node class name
        # eg Comparison -> compile_Comparison
        cls = List if isinstance(node, List) else node.__class__
        try:
            function = self._compile_methods_cache[cls]
        except KeyError:
            function = getattr(self, f"compile_{cls.__name__}")
            self._compile_methods_cache[cls] = function
        value = function(node, s)

        # Support for recursion: if the compiler method returns a Node, we run
        # compile() again
        if isinstance(value, Node):
            node.__compiled__[s] = value = self.compile(value, s)
            return value

        assert isinstance(
            value, str
        ), f"Compiler.compile() should return str, not {type(value).__name__} for {type(node).__name__}"

        node.__compiled__[s] = value
        return value

    @abstractmethod
    def serialize(self, node: Node) -> Node:
        """Creates a node of type `TextType` that serializes `node`.

        Most methods will retrieve results as text; columns of different types
        thus need to be converted to text before being converted back to their
        original type.

        If `node` is NULL, the returned value can evaluate to NULL.
        """
        # On many DBMS, such as MySQL or SQLite, ints and bools can be used with
        # string functions such as SUBSTR() or LENGTH(), so we don't need to
        # modify anything; we can just return a copy with the new type
        # We convert blobs into hexadecimal however.

    def deserialize(self, node_type: NodeType, value: bytes) -> Any:
        """Converts `value` back to its original type."""
        # This is a default implementation that assumes that blobs are retrieved as hex
        # strings.
        match node_type:
            case TextType():
                return value.decode()
            case IntType():
                try:
                    return int(value)
                except ValueError:
                    raise ConversionError(f"Invalid integer value: {value!r}")
            case BoolType():
                if value not in b"01":
                    raise ConversionError(f"Invalid boolean value: {value!r}")
                return value == b"1"
            case BlobType():
                try:
                    return bytes.fromhex(value.decode())
                except (ValueError, UnicodeDecodeError):
                    raise ConversionError(f"Invalid hexadecimal value: {value!r}")
            case _:
                raise ConversionError(
                    f"Cannot deserialize type: {type(node_type).__name__}"
                )

    def adjust_query(self, query: Query) -> Query:
        """Adjusts a Query statement to the DBMS. Columns with unknown types get
        converted to another node type.
        """
        return query.columns(
            *(self._adjust_column(column) for column in query.q.columns)
        )

    @abstractmethod
    def _adjust_column(self, column: Node) -> Node:
        """Converts columns with unknown types to another node type."""

    def wrap(self, node: Node, force: bool = False) -> Node:
        """Associates the node with this compiler. After this, when the node is
        converted to SQL syntax using `str` or `format`, it will be compiled
        using this compiler.

        Returns:
            Node: The same node.
        """
        node._forward_compiler(self, force=force)
        return node

    # Nodes

    @abstractmethod
    def compile_Query(self, query: Query, s: str) -> str:
        """Compiles a SELECT query. This is very DBMS-specific and is as such
        left to the responsibility of subclasses.
        """

    def compile_Limit(self, limit: Limit, s: str) -> str:
        match limit:
            case Limit(0, y):
                return f"LIMIT {y}"
            case Limit(x, y):
                return f"LIMIT {x},{y}"

    def compile_Alias(self, alias: Alias, s: str) -> str:
        if isinstance(alias.node, Query):
            node = f"({alias.node})"
        else:
            node = f"{alias.node:p}"
        return f"{node} AS {alias.alias}"

    def compile_Not(self, not_: Not, s: str) -> str:
        return f"NOT ({not_.node})"

    def compile_Star(self, star: Star, s: str) -> str:
        return "*"

    def compile_Cast(self, cast: Cast, s: str) -> str:
        return Function["CAST"](Expr("{:p} AS {}", cast.node, cast.datatype))

    def compile_Comparison(self, comparison: Comparison, s: str) -> str:
        return f"{comparison.left:p}{comparison.operator}{comparison.right:p}"

    def compile_IsIn(self, is_in: IsIn, s):
        negation = "NOT " if is_in.negate else ""
        return f"{is_in.needle:p} {negation}IN ({is_in.haystack})"

    @parenthesize
    def compile_Case(self, case: Case, s: str) -> str:
        if case.condition:
            format = f"CASE {case.condition:p}"
        else:
            format = "CASE"

        for value, result in case.cases:
            format += f" WHEN {value:p} THEN {result:p}"

        if case.default:
            format += f" ELSE {case.default:p}"

        return format + " END"

    def compile_Concatenation(self, concatenation: Concatenation, s: str) -> str:
        if not concatenation.nodes:
            return Value("")
        if len(concatenation.nodes) == 1:
            return format(concatenation.nodes[0], "p")
        return Function["CONCAT"](*concatenation.nodes)

    def compile_Count(self, count: Count, s: str) -> str:
        if count.distinct:
            return Function["COUNT"](Expr("DISTINCT {}", count.column))
        return Function["COUNT"](count.column)

    def compile_Hex(self, hex: Hex, s: str) -> str:
        return Function["HEX"](hex.node)

    def compile_Identifier(self, identifier: Identifier, s: str) -> str:
        return f"{identifier.name}"

    def compile_Expression(self, expression: Expression, s: str) -> str:
        if not expression.args:
            return expression.expression
        return expression.expression.format(*expression.args)

    @parenthesize
    def compile_Union(self, union: Union, s: str) -> str:
        jointure = "UNION" if not union.all else "UNION ALL"
        return f"{union.left} {jointure} {union.right}"

    @parenthesize
    def compile_LogicalOperation(self, operator: LogicalOperation, s: str) -> str:
        return f"{operator.left:p} {operator.operator} {operator.right:p}"

    @parenthesize
    def compile_ArithmeticOperation(self, operator: ArithmeticOperation, s: str) -> str:
        return f"{operator.left:p}{operator.operator}{operator.right:p}"

    def compile_Function(self, function: Function, s: str) -> str:
        return f"{function.name}{function.args:p}"

    @parenthesize
    def compile_List(self, lst: List, s: str) -> str:
        return ",".join(f"{item:p}" for item in lst.items)

    def compile_Order(self, order: Order, s: str) -> str:
        if order.reverse:
            return f"{order.node} DESC"
        return f"{order.node}"

    @parenthesize
    def compile_IsNull(self, is_null: IsNull, s: str) -> str:
        negation = "NOT " if is_null.negate else ""
        return f"{is_null.node:p} IS {negation}NULL"

    def compile_Value(self, value: Value, s: str) -> str:
        match value.value:
            case str():
                return self.quote(value.value)
            case bool() as val:
                return "1" if val else "0"
            case int() | float() as val:
                return str(val)
            case None:
                return "NULL"
            case val:
                raise TypeError(f"Cannot compile Value for type {type(val).__name__}")

    def compile_WordedComparison(self, comparison: WordedComparison, s: str) -> str:
        return f"{comparison.left:p} {comparison.operator} {comparison.right:p}"

    # Meta

    def compile_Length(self, length: Length, s):
        match length.node.metadata.type:
            case TextType():
                return Function["CHAR_LENGTH"](length.node)
            case _:
                return Function["LENGTH"](length.node)

    def compile_Substring(self, substring: Substring, s, function="SUBSTRING"):
        args = [substring.string, substring.start + 1]
        if substring.length is not None:
            args.append(substring.length)
        return Function[function](*args)

    def compile_Ord(self, ascii: Ord, s):
        return Function["ORD"](ascii.node)

    def compile_ConcatWS(self, cws: ConcatWS, s):
        if not cws.nodes:
            return Concatenation([])

        first, *rest = cws.nodes
        args = [first]
        for item in rest:
            args += [cws.separator, item]

        return Concatenation(args)


class HasConcatWSMixin:
    """Implements the real `CONCAT_WS` function instead of the standard wrapper.
    This drastically reduces the size of payloads.
    """

    def compile_ConcatWS(self, cws: ConcatWS, s):
        if not cws.nodes:
            return Value("")
        if len(cws.nodes) == 1:
            return f"{cws.nodes[0]:p}"
        return Function["CONCAT_WS"](cws.separator, *cws.nodes)
