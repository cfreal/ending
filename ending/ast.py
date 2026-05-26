"""Collection of AST `ending.ast.Node`s and `ending.ast.NodeType`s.

# Usage

## Creating nodes

Putting nodes together builds an SQL AST.

    # SQL: role = 'admin'
    node = Identifier('role') == Value('admin')
    # SQL: SELECT username, password FROM users LIMIT 3
    node = Query('users').columns('username', 'password').limit(3)
    # SQL: CAST(id AS text)
    node = Cast(Identifier('id'), 'text')

## Compiling to SQL syntax

This AST can then be converted to SQL syntax using a
`ending.db.generic.compiler.Compiler`.

    >>> node = (Identifier('role') == Value('admin')) & (Identifier('active') == Value(True))
    >>> compiler.compile(node)
    role='admin' AND active=1

## Typing

Nodes have types, represented as `NodeType` instances. They represent text (`TextType`),
integers (`IntType`), booleans (`BoolType`), and blob (`BlobType`). Types are used to
serialize data and perform SQL injections efficiently. They are deduced when possible,
but can be set using `Node.with_type`.

# Creating a query

Creating an SQL query:

    >>> # SQL: SELECT username, password, email FROM users
    >>> q = Query('users').columns('username', 'password', 'email')

Creating a condition:

    >>> # SQL: role=3 OR role=4 OR role_name LIKE '%admin%'
    >>> role = Identifier('role')
    >>> condition = (role == 3) | (role == 4) | Identifier('role_name').like('%admin%')

Creating new query from the original query and a condition:

    >>> # SQL: SELECT username, password, email FROM users WHERE role=3 OR role=4 OR role_name LIKE '%admin%'
    >>> q2 = q.where(condition)

# Creating a blind SQL injection payload

Let's construct a generic SQL injection payload using nodes, and then translate
it into any DBMS-specific syntax.

We want to obtain the hex-encoded password of the first user who has role
`admin`:

    q = Query('users').columns('password').where(Identifier('role') == 'admin').limit(1)

We take the first character of the password:

    s = Substring(q, 0, 1)

And compare it to the first 8 hexadecimal chars `0..7`:

    i = IsIn(s, ['0','1','2','3','4','5','6','7'])

Using OOP, we can even one-line this whole process:

    q = Query('users').columns('password').where(Identifier('role') == 'admin').limit(1)
    s = Substring(q, 0, 1)
    i = s.is_in('01234567')

Now that we have our AST, let's compile it into valid syntax for a few DBMSs, for instance MySQL and Microsoft SQL:

    >>> mysql.Compiler().compile(i)
    "SUBSTR((SELECT password FROM users WHERE role='admin' LIMIT 0,1),1,1) IN ('0','1','2','3','4','5','6','7')"
    >>> mssql.Compiler().compile(i)
    "SUBSTRING((SELECT password FROM users WHERE role='admin' ORDER BY password OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY),1,1) IN ('0','1','2','3','4','5','6','7')"

# Metanodes

Metanodes represent nodes that are not a real SQL AST node, but can be
represented (differently) accross DBMSes.

An example is `Length`, that returns the length of a string. It represents the
`LENGTH()` function under MySQL, but `LEN()` in Microsoft SQL. The same goes for
`Substring`, which can be either the `SUBSTRING()` or the `SUBSTR` function.
"""

from __future__ import annotations

from copy import copy
import functools
import string
from abc import ABC, abstractmethod
from dataclasses import (
    KW_ONLY,
    InitVar,
    dataclass,
    field,
    fields,
    is_dataclass,
    replace,
)
from inspect import isclass
from typing import (
    Any,
    ClassVar,
    Generic,
    Iterator,
    Protocol,
    Type,
    TypeVar,
    Union,
)

from ending.exception import CompilerNotSetError, NodeTypeError
from ending.util import randomized
from ending.util.freezable import Freezable
from ending.util.misc import repr_attrs

__all__ = [
    "Query",
    "QueryParts",
    "Identifier",
    "Value",
    "Expr",
    "Alias",
    "Ord",
    "Cast",
    "Case",
    "Comparison",
    "WordedComparison",
    "Concatenation",
    "ConcatWS",
    "Count",
    "Expression",
    "Function",
    "Hex",
    "Not",
    "IsIn",
    "IsNull",
    "Length",
    "List",
    "Limit",
    "Operation",
    "BooleanOperation",
    "LogicalOperation",
    "ArithmeticOperation",
    "Order",
    "Substring",
    "Union",
    "Star",
    "node",
    "UnknownType",
    "BoolType",
    "IntType",
    "TextType",
    "BlobType",
    "NodeType",
    "Node",
    "CastableNode",
]

# -- Types


class NodeType(ABC, Freezable):
    """The type of an SQL AST `ending.ast.Node`."""

    __slots__ = []

    def __repr__(self):
        return repr_attrs(self, ())

    def feedback(self, value: Any) -> None:
        """If a value was obtained for this type, it will be sent back using
        this function in order to build statistics. These will help guess the
        upcoming values.
        """

    @abstractmethod
    def to_texttype(self) -> TextType:
        """Returns an equivalent `TextType` instance."""


class UnknownType(NodeType):
    """Unknown type."""

    def to_texttype(self) -> TextType:
        return TextType()


class BoolType(NodeType):
    """Boolean type."""

    def to_texttype(self, false: str = "0", true: str = "1") -> TextType:
        """Returns an equivalent `TextType` instance."""
        return TextType(charset=false + true, size=1)


class IntType(NodeType):
    """Integer type.

    Attributes:
        min (int): Minimum possible value, inclusive.
        max (int): Maximum possible value, inclusive.

    The `value=N` constructor parameter is an alias for `min=N, max=N`.
    """

    __slots__ = ["min", "max", "previous_values"]

    min: int
    max: int

    def __init__(self, min: int = None, max: int = None, value: int = None):
        if value is not None:
            self.min = value
            self.max = value
        else:
            self.min = min
            self.max = max

        self.previous_values = []
        self._freeze()

    def __repr__(self):
        return repr_attrs(self, ["min", "max"])

    def feedback(self, value: int):
        self.previous_values.append(value)

    def to_texttype(self) -> TextType:
        """Returns an equivalent `TextType` instance."""
        min = len(str(self.min)) if self.min is not None else 0
        max = len(str(self.max)) if self.max is not None else None
        return TextType(charset="1234567890", size=IntType(min, max))


class TextType(NodeType):
    """Represents a string type, such as `VARCHAR(n)`, `TEXT`, etc.

    Attributes:
        size (IntType): Bounds for the size of the string
        charset (str): List of possible values. Defaults to printable ASCII.
    """

    charset: str = string.printable
    size: IntType

    def __init__(
        self,
        charset: str = None,
        size: IntType = None,
    ):
        if charset is not None:
            if not isinstance(charset, str):
                raise TypeError(
                    f"TextType: charset should be str, not {type(charset).__name__}"
                )
            self.charset = "".join(dict.fromkeys(charset))

        if isinstance(size, int):
            size = IntType(min=size, max=size)
        elif size is None:
            size = IntType(min=0, max=None)

        self.size = size
        self._freeze()

    def __repr__(self):
        return repr_attrs(self, ["charset", "size"])

    def to_texttype(self) -> TextType:
        return self


class BlobType(NodeType):
    """Represents a byte type, such as `BLOB`, `BINARY`.

    Attributes:
        size (IntType): Bounds for the size of the string
        byteset (bytes): List of possible values. Defaults to [0, 255].
    """

    __slots__ = ["byteset", "size"]

    byteset: bytes
    size: IntType

    def __init__(
        self,
        byteset: bytes = None,
        size: IntType = None,
    ):
        if byteset is not None:
            if not isinstance(byteset, (bytes, bytearray)):
                raise TypeError(
                    f"BlobType: byteset should be bytes, not {type(byteset).__name__}"
                )
            self.byteset = bytes(dict.fromkeys(byteset))
        else:
            self.byteset = bytes(range(256))

        if isinstance(size, int):
            size = IntType(min=size, max=size)
        elif size is None:
            size = IntType(min=0, max=None)

        self.size = size
        self._freeze()

    def to_texttype(self) -> TextType:
        # We expect the value to be cast as hexadecimal.
        size = IntType(
            min=(self.size.min * 2 if self.size.min is not None else 0),
            max=(self.size.max * 2 if self.size.max is not None else None),
        )
        return TextType(charset=string.hexdigits, size=size)


# Nodes


@dataclass
class NodeAttributes:
    """Holds information about the attributes of a node class. This allows proper
    typechecking and casting on runtime. Computing these preemptively instead of
    iterating over the fields of a node class every time a node is created provides
    enormous performance improvements.
    """

    nodes: set[tuple[str, type]] = field(default_factory=set)
    """List of standard nodes attributes and their types."""
    castable_nodes: set[tuple[str, type]] = field(default_factory=set)
    """List of nodes that can be created from a standard value and their type."""
    standard: set[tuple[str, type]] = field(default_factory=set)
    """List of standard attributes."""
    names: set[str] = field(default_factory=set)
    """Names of every child node."""


def metadata_property(func):
    name = func.__name__
    compute = f"_compute_{name}"

    @functools.wraps(func)
    def func(self) -> Any:
        if name not in self._cache:
            self._cache[name] = value = getattr(self._node, compute)(self)
            return value
        return self._cache[name]

    return func


class Metadata(Freezable):
    """Node metadata.

    Attributes:

    - type: the SQL type of the node result, as a `NodeType` instance
    - single: whether the node returns a single row or multiple rows
    - nullable: whether the node can be NULL or not

    Metadata attributes can be set by passing them as keyword arguments to the `Node`
    constructor, or by using the `Node.with_metadata` method, which returns a copy of
    the node with updated metadata values.

    If an attribute is not set, it will be computed on first access using the
    corresponding `_compute_{attribute}` method of the node, and cached for subsequent
    accesses. This allows to compute metadata lazily, which can be a great performance
    improvement, as in many cases, metadata is not needed at all.
    """

    def __init__(
        self,
        node: Node,
        type: NodeType = None,
        single: bool = None,
        nullable: bool = None,
    ) -> None:
        self.__node = node
        if type is not None:
            self.type = type
        if single is not None:
            self.single = single
        if nullable is not None:
            self.nullable = nullable
        self._freeze()

    def with_(self, **kwargs) -> Metadata:
        new = Metadata(self.__node)
        new.__dict__.update(**self.__dict__, **kwargs)
        return new

    @functools.cached_property
    def type(self) -> NodeType:
        """SQL type of result. Cached after first compute."""
        return self.__node._compute_type()

    @functools.cached_property
    def single(self) -> bool:
        """Whether node returns single row. Cached after first compute."""
        return self.__node._compute_single()

    @functools.cached_property
    def nullable(self) -> bool:
        """Whether result can be NULL. Cached after first compute."""
        return self.__node._compute_nullable()


@dataclass(eq=False)
class Node(Freezable):
    """Base class for SQL AST nodes.

    # Attributes

    When creating a node, its attributes are automatically casted into node objects if
    they are not already.

    For instance, if a node has an attribute `value` of type `Value`, and we create it
    with `value=3`, the constructor will automatically cast `3` to `Value(3)`. If an
    attribute cannot be casted to its appropriate type, a `NodeTypeError` is raised.

    # Metadata

    In addition to its branches, a node also has metadata, represented by a `Metadata`
    instance stored in the `metadata` attribute. This contains three piece of
    information about the node: its SQL `type`, whether it returns a `single` row or
    multiple rows, and whether it can be NULL or not (`nullable`).

    Metadata attributes are computed on demand using the corresponding
    `_compute_{attribute}` method of the node, and cached for subsequent accesses. This
    allows to compute metadata lazily, which can be a great performance improvement, as
    in many cases, metadata is not needed at all.

    They can also be set manually by passing them as keyword arguments to the node
    constructor, or by using the `Node.with_metadata` method, which returns a copy of the
    node with updated metadata values. Setting metadata manually can be useful when the
    metadata cannot be computed from the node structure, for instance when it depends on
    the context of the query.

    For instance, a `Count` node has a `type` of `IntType`, a `single` value of `True`,
    and a `nullable` value of `False`.

    # Immutability

    Nodes are immutable, and their attributes cannot be changed after creation. However,
    they can be copied using the `replace` function from the `dataclasses` module, or by
    using the `Node.with_metadata` method to create a copy with updated metadata values.
    """

    # Node metadata
    _: KW_ONLY
    type: InitVar[NodeType] = None
    """SQL type of the node result."""
    single: InitVar[bool] = None
    """Whether the node returns a single row or multiple rows."""
    nullable: InitVar[bool] = None
    """Whether the node can be NULL or not."""

    __compiler__ = None
    """Holds a reference to the compiler that will be used to compile the node."""
    __compiled__ = None
    """Holds the compiled version of the node in function of the format
    specifier. Caching this provides great performance improvements.
    """
    __node_attrs__: ClassVar[NodeAttributes]
    """Description of the node attributes."""

    def __post_init__(self, type: NodeType, single: bool, nullable: bool) -> None:
        """Casts attributes their appropriate type and freezes the object."""
        self.__compiled__ = {}
        if type is not None or single is not None or nullable is not None:
            self.metadata = Metadata(self, type=type, single=single, nullable=nullable)
        self._set_fields()
        self._freeze()

    def _set_fields(self) -> None:
        """Checks fields have their appropriate types."""
        attrs = self.__node_attrs__

        # For normal node types, just check that we have a Node
        for field, type in attrs.nodes:
            value = getattr(self, field)
            if value is not None and not isinstance(value, Node):
                raise NodeTypeError(self, field, type, value)

        # For others, if we don't have a node, cast the raw value to a node type
        for field, type in attrs.castable_nodes:
            value = getattr(self, field)
            if value is not None and not isinstance(value, Node):
                try:
                    setattr(self, field, type.from_value(value))
                except TypeError:
                    raise NodeTypeError(self, field, type, value)

        # And finally, for standard attributes, just check the type
        for field, type in attrs.standard:
            value = getattr(self, field)
            if value is not None and not isinstance(value, type):
                raise NodeTypeError(self, field, type, value)

    def _forward_compiler(self, compiler, force: bool) -> None:
        """Forwards the compiler to the node and its children."""
        # Since nodes are immutable, if one node has a compiler set, its
        # children do as well.
        if self.__compiler__ and not force:
            return

        object.__setattr__(self, "__compiler__", compiler)

        for node in self.__node_attrs__.names:
            node = getattr(self, node)
            if node:
                node._forward_compiler(compiler, force=force)

    @functools.cached_property
    def metadata(self) -> Metadata:
        """Lazy metadata view. Each property computes on first access and caches."""
        return Metadata(self)

    def _compute_single(self) -> bool:
        """Whether node returns single row. Override in subclasses."""
        return False

    def _compute_nullable(self) -> bool:
        """Whether value can be NULL. Override in subclasses."""
        return True

    def _compute_type(self) -> NodeType:
        """SQL type of result. Override in subclasses."""
        return UnknownType()

    def with_type(self, type: NodeType) -> Node:
        """Returns a copy with updated type metadata."""
        return self.with_metadata(type=type)

    def with_metadata(
        self, type: NodeType = None, single: bool = None, nullable: bool = None
    ) -> Node:
        """Returns a copy with updated metadata values.

        Args:
            single: Override single
            nullable: Override nullable
            type: Override type

        Examples:
            >>> node = Identifier('id')
            >>> node.with_metadata(type=TextType())
            >>> node.with_metadata(nullable=True)
        """
        kwargs = {
            "type": type,
            "single": single,
            "nullable": nullable,
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        return replace(self, **kwargs)

    def __format__(self, spec) -> str:
        """Builds the SQL expression using the AST Builder."""
        try:
            compile = self.__compiler__.compile
        except AttributeError:
            raise CompilerNotSetError() from None
        return compile(self, spec)

    @staticmethod
    def node_cast(item: Any, cls: Type[Node], cast_none: bool = False) -> Node:
        """If given item is not a `Node` or `None`, wraps it into given node
        type.

        Args:
            item: Value to be casted if it is not a node
            cls: Node class to cast value into
            cast_none: If true, `None` items will be cast. If false, `None` will
                be returned. Default to false.

        Returns:
            If the item is a node or `None`, the item.
                Otherwise, the item wrapped in given node class.
        """
        if (item is None and not cast_none) or isinstance(item, Node):
            return item
        # Can raise TypeError
        return cls(item)

    def __str__(self):
        return f"{self}"

    # Operations: Comparison & Logical

    def __eq__(self, other) -> Comparison:
        return Comparison(self, "=", other)

    def __ne__(self, other) -> Comparison:
        return Comparison(self, "!=", other)

    def __lt__(self, other) -> Comparison:
        return Comparison(self, "<", other)

    def __le__(self, other) -> Comparison:
        return Comparison(self, "<=", other)

    def __gt__(self, other) -> Comparison:
        return Comparison(self, ">", other)

    def __ge__(self, other) -> Comparison:
        return Comparison(self, ">=", other)

    def __and__(self, other) -> LogicalOperation:
        return LogicalOperation(self, "AND", other)

    def __or__(self, other) -> LogicalOperation:
        return LogicalOperation(self, "OR", other)

    def __hash__(self) -> int:
        """Prevents the use of nodes in dicts or sets, as their __eq__ method is
        not what python expects it to be.
        """
        raise TypeError(f"Node objects are not hashable")

    # Other useful operators

    def like(self, other: Node) -> WordedComparison:
        """Equivalent to the SQL `LIKE` close.

        Wrapper for `WordComparison`.
        """
        return WordedComparison(self, "LIKE", other)

    def is_in(self, haystack: List, negate: bool = False) -> IsIn:
        """Equivalent to the SQL `IS IN` close.

        Wrapper for `IsIn`.
        """
        return IsIn(self, haystack, negate=negate)


# TODO We add dataclass manually instead of using this alias, because Pylance
# does not detect the dataclass otherwise, and as a result code completion is garbage
def node(node_type: type[T]) -> type[T]:
    """A decorator that checks the types of the attributes of a node, and sets its
    `Node.__node_attrs__` attribute if necessary.

    It must be called onto every new node class.
    """
    if not issubclass(node_type, Node):
        raise TypeError("node() can only be used on subclasses of Node")

    # This verifies that the topmost class is a dataclass
    # TODO Remove when we can get rid of the explicit @dataclass decorator
    if (
        not is_dataclass(node_type)
        or node_type.__dataclass_fields__.keys() != node_type.__annotations__.keys()
    ):
        node_type = dataclass(node_type, eq=False)

    # Evaluate type annotations if required
    for field in fields(node_type):
        try:
            field.type = eval(field.type)
        except TypeError:
            pass

    # In some cases, it can be set manually
    if hasattr(node_type, "__node_attrs__"):
        return node_type

    # If not, we fill it ourselves

    attrs = NodeAttributes()

    for field in fields(node_type):
        tpe = field.type

        if tpe is Any:
            continue

        if tpe in (Any, str, int, bool):
            attrs.standard.add((field.name, tpe))
            continue

        if isclass(tpe):
            if issubclass(tpe, CastableNode):
                attrs.names.add(field.name)
                attrs.castable_nodes.add((field.name, tpe))
                continue
            if issubclass(tpe, Node):
                attrs.names.add(field.name)
                attrs.nodes.add((field.name, tpe))
                continue
            if issubclass(tpe, NodeType):
                continue

        raise NodeTypeError(node_type, field.name, "valid type", tpe)

    node_type.__node_attrs__ = attrs

    return node_type


T = TypeVar("T", bound=Node)


class CastableNode(Node):
    @classmethod
    def from_value(cls, value: Any) -> Node:
        if isinstance(value, Node):
            return value
        return cls(value)


@node
@dataclass(eq=False)
class List(Generic[T], CastableNode):
    """`(1, 2, 'a', ...)`

    Examples:
        >>> # 0x61,0x62,0x63,0x64
        >>> List[Value](['a', 'b', 'c', 'd'])
        >>> # 1,2,123,768,0x74657374
        >>> List[Value]([1, 2, 123, 0x300, 'test'])
        >>> # 0x61,0x62,0x63,0x64,0x65,0x66,0x67,0x68,0x69
        >>> List[Value]('abcdefghi')
        >>> # 97,98,99,100,101,102,103,104,105
        >>> List[Value](map(ord, 'abcdefghi'))
    """

    __type__: ClassVar[type[T]]
    """Node type to cast untyped elements into."""
    __node_attrs__: ClassVar[NodeAttributes] = NodeAttributes()

    items: tuple[T, ...]

    def __class_getitem__(cls, item):
        # return a lightweight subclass that remembers the parameter
        name = f"{cls.__name__}[{getattr(item, '__name__', item)}]"
        return type(name, (cls,), {"__type__": item})

    def _set_fields(self) -> None:
        try:
            item_type = self.__class__.__type__
        except AttributeError:
            raise TypeError(
                "List type not set: always instanciate lists with List[...]"
            )
        self.items = tuple(Node.node_cast(item, item_type, True) for item in self.items)

    def _forward_compiler(self, compiler, force: bool) -> None:
        if self.__compiler__ and not force:
            return

        object.__setattr__(self, "__compiler__", compiler)

        for item in self.items:
            item._forward_compiler(compiler, force=force)

    def __repr__(self) -> str:
        return f"List[{self.__type__.__name__}]({self.items!r})"

    def _compute_single(self) -> bool:
        return all(node.metadata.single for node in self.items)

    def _compute_nullable(self) -> bool:
        return any(node.metadata.nullable for node in self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, key: int) -> T:
        return self.items[key]

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


@node
@dataclass(eq=False)
class Value(CastableNode):
    """SQL value: contains an SQL value, such as a string, an integer, or a
    boolean.

    When converted to SQL using `format` or `str`, it will be formatted in
    accordance to the configuration of the compiler. Numerical values (`int`)
    will not be changed. `None` will be converted to `NULL`.
    String values will be converted in accordance to the string formatting of
    the `ending.db.generic.compiler.Compiler`.

    For instance, the compiler might convert strings to:

    * singlequote: `'ABC'`
    * doublequote: `"ABC"`
    * hexadecimal: `0x414243`
    * char: `CONCAT(CHAR(65),CHAR(66),CHAR(67))`

    Examples:

        >>> # 123
        >>> Value(123)
        >>> # 'some_string'
        >>> Value('some_string')
        >>> # NULL
        >>> Value(None)
    """

    value: Any
    """Scalar python value to convert to SQL (`int`, `str`, etc.)."""

    def _compute_single(self) -> bool:
        return True

    def _compute_nullable(self) -> bool:
        # It doesn't get any more nullable than NULL
        return self.value is None

    def _compute_type(self) -> NodeType:
        v = self.value
        match v:
            case None:
                return UnknownType()
            case bool():
                return BoolType()
            case int():
                return IntType(value=v)
            case str():
                return TextType(charset=v, size=len(v))
            case bytes():
                return BlobType(byteset=v, size=len(v))
            case _:
                raise ValueError(f"Cannot determine SQL type for value {v!r}")


@node
@dataclass(eq=False)
class Identifier(CastableNode):
    """SQL identifier, such as a table name, or column name.

    Examples:
        >>> # name
        >>> Identifier('name')
    """

    name: str
    """Name of the identifier."""

    def _compute_type(self) -> NodeType:
        return UnknownType()


@node
@dataclass(eq=False)
class Expression(CastableNode):
    """SQL Expression.

    A raw expression and its parameters. Useful to generate quick SQL snippets.
    Allows to quickly format parts of an SQL expression, without having to
    decompose the whole syntax into a tree.

    Note: Generally, it's better to build SQL expressions using nodes instead
    of this. This is the equivalent of harcoding an address: it might work in
    your case but this is not the most generic way.

    Examples:
        >>> # role_id=1 OR username LIKE 'test'
        >>> Expression('role_id={} OR username LIKE {}', (1, 'test'))
        >>> # first_name='John' OR last_name='John'
        >>> Expression('first_name={0} OR last_name={0}', ('John',))
        >>> # id > 13 OR id < 8
        >>> Expression('{} OR {}', ((Identifier('id') > 13), (Identifier('id') < 8)))
    """

    expression: str
    """A format expression."""
    args: List[Value] = List[Value](())
    """Arguments for the expression."""

    def _compute_type(self) -> UnknownType:
        return UnknownType()


def Expr(expression: str, *args: Value, type: NodeType = None) -> Expression:
    """Syntaxic sugar for `Expression` that allows to specify arguments inline.

    Examples:

        The two next lines are equivalent:

            >>> Expr('mail={} OR user={} AND role={}', 'test@test.net', 'test', Value(123))
            >>> Expression('mail={} OR user={} AND role={}', ['test@test.net', 'test', Value(123)])
    """
    added_type = {"type": type} if type is not None else {}
    return Expression(expression, args, **added_type)


@node
@dataclass(eq=False)
class Not(Node):
    """`NOT <node>`"""

    node: Node

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable

    def _compute_type(self) -> NodeType:
        return BoolType()


@node
@dataclass(eq=False)
class Alias(Node):
    """`<node> AS <alias>`"""

    node: Identifier
    """Node to alias."""
    alias: Identifier
    """Alias name."""

    @classmethod
    def randomized(cls, node: Identifier, *, size: int = 4) -> Alias:
        """Returns an alias of `node` with a randomized name"""
        alias_name = Identifier(randomized.identifier(size))
        return cls(node=node, alias=alias_name)

    def __post_init__(self, type: NodeType, single: bool, nullable: bool) -> None:
        super().__post_init__(type, single, nullable)

        # Transfer the type of the node to the alias, if required
        if isinstance(self.alias.metadata.type, UnknownType):
            object.__setattr__(
                self, "alias", self.alias.with_type(self.node.metadata.type)
            )

    def _compute_type(self) -> NodeType:
        return self.node.metadata.type

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


@node
@dataclass(eq=False)
class Cast(Node):
    """`CAST(<node> AS <datatype>)`"""

    node: Identifier
    """Node to CAST."""
    datatype: Expression
    """Type to cast to."""

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


@node
@dataclass(eq=False)
class Operation(Node, ABC):
    """Base class for operations. Operations have two operands and an operator."""

    left: Value
    """Left operand"""
    operator: str
    """Operator: `AND`, `REGEXP`, `+`, `-`, `*`, `/`, ..."""
    right: Value
    """Right operand"""

    def _compute_single(self) -> bool:
        return self.left.metadata.single and self.right.metadata.single

    def _compute_nullable(self) -> bool:
        return self.left.metadata.nullable or self.right.metadata.nullable


@node
@dataclass(eq=False)
class BooleanOperation(Operation):
    """Operations that return a boolean value, such as `AND` and `OR`, but also `LIKE`,
    etc.
    """

    def _compute_type(self) -> NodeType:
        return BoolType()


@node
@dataclass(eq=False)
class LogicalOperation(BooleanOperation):
    """Logical operation: `AND` or `OR`."""


@node
@dataclass(eq=False)
class ArithmeticOperation(Operation):
    """Arithmetic operations such as `+`, `-`, etc. Returns a node of integer type."""

    def _compute_type(self) -> NodeType:
        return IntType()


@node
@dataclass(eq=False)
class Comparison(BooleanOperation):
    """Compares two operands.
    `<left> <operator> <right>`

    Examples:
        >>> # 1=2
        >>> Comparison(1, '=', 2)
        >>> # id >= 2
        >>> Comparison(Identifier('id'), '>=', 2)
        >>> # username!='test'
        >>> Identifier('username') != 'test'
        >>> # username!=firstname
        >>> Identifier('username') != Identifier('firstname')
    """


@node
@dataclass(eq=False)
class WordedComparison(Comparison):
    """A comparison between `left` and `right` with a word operator such as
    `LIKE`, `GLOB`, or `REGEXP`.

    SQL: `<left> <LIKE|REGEXP|GLOB> <right>`
    """


@node
@dataclass(eq=False)
class Star(Node):
    """*"""


@node
@dataclass(eq=False)
class Case(Node):
    """`CASE[ <condition>] WHEN <value> THEN <result> [ELSE <default>] END`"""

    condition: Node
    """Condition to check, or None."""
    cases: tuple[tuple[Value, Value]]
    """value -> result mappings."""
    default: Value = None
    """Default value if condition is false."""

    # Note: cases is not included as it has a non-default type
    __node_attrs__: ClassVar[NodeAttributes] = NodeAttributes(
        nodes=[("condition", Node)],
        castable_nodes=[("default", Value)],
        names={"condition", "default"},
    )

    def _set_fields(self) -> None:
        super()._set_fields()
        cast = Value.from_value
        self.cases = tuple((cast(value), cast(result)) for value, result in self.cases)

    def _forward_compiler(self, compiler, force: bool) -> None:
        for value, result in self.cases:
            value._forward_compiler(compiler, force=force)
            result._forward_compiler(compiler, force=force)

        return super()._forward_compiler(compiler, force=force)

    def _compute_single(self) -> bool:
        return (
            (not self.condition or self.condition.metadata.single)
            and (self.default is None or self.default.metadata.single)
            and all(
                case.metadata.single and result.metadata.single
                for case, result in self.cases
            )
        )

    def _compute_nullable(self) -> bool:
        return (
            (self.condition and self.condition.metadata.nullable)
            or (self.default and self.default.metadata.nullable)
            or any(
                case.metadata.nullable or result.metadata.nullable
                for case, result in self.cases
            )
        )


@node
@dataclass(eq=False)
class Count(Node):
    """`COUNT([DISTINCT] <column>)`"""

    column: Identifier = field(default_factory=Star)
    """The column to include in the clause. If empty, the node translates to `COUNT(*)`.
    """
    distinct: bool = False
    """If set, count distinct values."""

    def _compute_type(self) -> NodeType:
        return IntType(min=0)

    def _compute_single(self) -> bool:
        return True

    def _compute_nullable(self) -> bool:
        return False


class FunctionProtocol(Protocol):
    def __call__(
        self, *args: Value, type: NodeType, single: bool = None, nullable: bool = None
    ) -> Function: ...


class MetaFunction(type):
    def __getitem__(self, function: str) -> FunctionProtocol:
        @functools.wraps(Function)
        def make_function(
            *arguments,
            type: NodeType = None,
            single: bool = None,
            nullable: bool = None,
        ) -> Function:
            return Function(
                function, arguments, type=type, single=single, nullable=nullable
            )

        return make_function


@node
@dataclass(eq=False)
class Function(Node, metaclass=MetaFunction):
    """Function call: `<name>([arguments...])`.

    Usage:

        Function[name](arg1, arg2, ...)

    Example:

        >>> # SQL: LENGTH("password")
        Function['LENGTH'](Identifier("password"))
        >>> # SQL: SUBSTR(version(),3,4)
        >>> Function["SUBSTRING"](Function["version"](), 3, 4)
    """

    name: str
    """Function name."""
    args: List[Value]
    """Function arguments."""

    def _compute_single(self) -> bool:
        # If every argument is single, we can expect a single result. Otherwise, we
        # can't be sure.
        return self.args.metadata.single


@node
@dataclass(eq=False)
class IsNull(Node):
    """`<node> IS [NOT] NULL`"""

    node: Identifier
    negate: bool = False

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_type(self) -> NodeType:
        return BoolType()

    def _compute_nullable(self) -> bool:
        return False


@node
@dataclass(eq=False)
class Order(Node):
    """`<node>[ <ASC|DESC>]`"""

    node: Identifier
    reverse: bool = False


#
# MetaNodes
#
# Meta nodes are nodes that do not need to exist at the AST-level, because they
# other nodes cover their usage, but DO need to exist at the DBMS-level,
# because they need to built differently for each DBMS.
#
# As an example, Length(node) could be built using Function('LENGTH', [node]),
# but some DBMS do not implement the `LENGTH()` function (they implement `LEN()`
# instead.
#
# Therefore, keeping a level of abstraction helps making the code generic.
#


@node
@dataclass(eq=False)
class Length(Node):
    """Mesures the length of `node` (Metanode).

    If the node is of type text, it returns the length in characters.
    If the node is of type blob, it returns the length in bytes.
    """

    node: Identifier

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_type(self) -> NodeType:
        match self.node.metadata.type:
            case TextType(size=size) | BlobType(size=size):
                return size
            case _:
                return IntType(min=0)

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


@node
@dataclass(eq=False)
class Substring(Node):
    """`SUBSTR(<string>, <start>, <length>)` (Metanode)

    Indexing starts at `0` (zero).
    String can either be of texttype or chartype.
    """

    string: Identifier
    start: int
    length: int = None

    def _compute_single(self) -> bool:
        return self.string.metadata.single

    def _compute_type(self) -> NodeType:
        node_type = self.string.metadata.type

        if not isinstance(node_type, (TextType, BlobType)):
            return TextType(size=IntType(min=0, max=self.length))

        if node_type.size.max is not None:
            delta = max(0, node_type.size.max - self.start)
            if isinstance(self.length, int):
                size_max = min(delta, self.length)
            else:
                size_max = delta
        elif isinstance(self.length, int):
            size_max = self.length
        else:
            size_max = None

        if node_type.size.min is not None:
            delta = max(0, node_type.size.min - self.start)
            if isinstance(self.length, int):
                size_min = min(delta, self.length)
            else:
                size_min = delta
        else:
            size_min = 0

        if isinstance(node_type, TextType):
            return TextType(
                charset=node_type.charset,
                size=IntType(size_min, size_max),
            )
        else:
            return BlobType(
                byteset=node_type.byteset,
                size=IntType(size_min, size_max),
            )

    def _compute_nullable(self) -> bool:
        return self.string.metadata.nullable


@node
@dataclass(eq=False)
class Concatenation(Node):
    """`CONCAT(<node0>, <node1>, ...)` (Metanode)

    Examples:
        >>> # CONCAT(0x41, 0x42, 0x43, 1234)
        >>> Concatenation(('A', 'B', 'C', 1234))
    """

    nodes: List[Value]
    """Nodes to be concatenated."""

    def _compute_single(self) -> bool:
        return self.nodes.metadata.single

    def _compute_type(self) -> NodeType:
        # We're not finding out the charset and size for the total payload
        # because when we concatenate, we generally don't care much for
        # these details
        return TextType()

    def _compute_nullable(self) -> bool:
        return self.nodes.metadata.nullable


@node
@dataclass(eq=False)
class IsIn(Node):
    """`<needle> [NOT ]IN (<haystack>)` (Metanode)

    Examples:
        >>> # 't' NOT IN ('a', 'b', 'c')
        >>> IsIn(Value('t'), List(['a', 'b', 'c']))
    """

    needle: Expression
    haystack: List[Value]
    negate: bool = False

    def _compute_type(self) -> BoolType:
        return BoolType()

    def _compute_single(self) -> bool:
        return self.needle.metadata.single and self.haystack.metadata.single

    def _compute_nullable(self) -> bool:
        # TODO Not completely accurate, as it depends on the operator and the DBMS
        # In MySQL: `'a' IN (NULL)` is NULL, but `'a' IN ('a', NULL)` is not.
        return self.needle.metadata.nullable or self.haystack.metadata.nullable


@node
@dataclass(eq=False)
class ConcatWS(Node):
    """`CONCAT_WS(<separator>, <nodes>...)` (Metanode)"""

    separator: Value
    nodes: List[Value]

    def _compute_type(self) -> NodeType:
        return TextType()

    def _compute_single(self) -> bool:
        return self.separator.metadata.single and self.nodes.metadata.single

    def _compute_nullable(self) -> bool:
        return self.nodes.metadata.nullable


@node
@dataclass(eq=False)
class Ord(Node):
    """Numerical value for node `node` (Metanode).

    If the node is of type text, it returns the unicode value of the first character.
    If the node is of type blob, it returns the value first byte.
    """

    node: Value

    def _compute_type(self) -> NodeType:
        # Do we need to specify a more specific range?
        match self.node.metadata.type:
            case BlobType():
                return IntType(min=0, max=255)
            case _:
                return IntType(min=0)

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


@node
@dataclass(eq=False)
class Hex(Node):
    """`HEX(<node>)` (Metanode)
    Returns the value as an hexadecimal string.

    `Hex(some_blob)` -> `414243`
    """

    node: Node

    def _compute_type(self) -> NodeType:
        match self.node.metadata.type:
            case BlobType(size=size):
                return TextType(
                    charset=string.hexdigits,
                    size=IntType(
                        min=(size.min * 2 if size.min is not None else 0),
                        max=(size.max * 2 if size.max is not None else None),
                    ),
                )
            case _:
                return TextType(charset=string.hexdigits)

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


@node
@dataclass(eq=False)
class Limit(Node):
    """SQL LIMIT keyword."""

    start: int
    count: int

    def get_start_count(self) -> tuple[int, int]:
        """Returns a tuple the start index and the number of rows to return."""
        return self.start, self.count

    def get_start_stop(self) -> tuple[int, int]:
        """Returns a tuple the first index (inclusive) and the last index (exclusive).

        For instance, (2, 5) means rows at index 2, 3, 4."""
        return self.start, self.start + self.count


@node
@dataclass(eq=False)
class QueryParts(Node):
    """Contains elements of a select query.
    Do not use this class directly, use `Query` instead.

    Attributes:
        table (Identifier): SQL table (`FROM`)
        columns (List[Identifier]): SQL columns (`SELECT`)
        distinct (bool): Whether to collect distinct rows only (`DISTINCT`)
        where (Expression): SQL condition (`WHERE`)
        order (Order): Order (`ORDER`)
        limit (Limit): Index and number of rows to return (`LIMIT`)

    """

    table: Identifier = None
    columns: List[Identifier] = List[Identifier](())
    distinct: bool = False
    where: Expression = None
    order: List[Order] = None
    limit: Limit = None


@node
@dataclass(init=False, eq=False)
class Query(Node):
    """SQL query: represents an SQL `SELECT` statement.

    Examples:
        >>> # SELECT version(),user(),database()
        >>> q = Query().columns('version()', 'user()', 'database()')
        >>> # SELECT col1,col2 FROM some_table
        >>> q = Query('some_table').columns('col1', 'col2')
        >>> # Build a new query from the previous one
        >>> # SELECT col1,col2 FROM some_table WHERE col3=3
        >>> q = q.where(Identifier('col3') == 3)
    """

    q: QueryParts
    """Query parts.
    """

    def __init__(
        self,
        table: tuple | list = None,
        *,
        q: QueryParts = None,
        type: NodeType = None,
        single: bool = None,
        nullable: bool = None,
    ):
        if q:
            self.q = q
        else:
            self.q = QueryParts(
                table=table, type=type, single=single, nullable=nullable
            )
        super().__init__()

    def _compute_type(self) -> NodeType:
        """If there only one column, take its type. Otherwise, type is unknown."""
        if len(self.q.columns) == 1:
            return self.q.columns[0].metadata.type
        return UnknownType()

    def _compute_single(self) -> bool:
        return not self.q.table or self.q.columns.metadata.single

    def _compute_nullable(self) -> bool:
        return self.q.columns.metadata.nullable

    def _mutate(self, **kwargs):
        """Creates a new SQL statement by replacing each `QueryParts` attribute
        by the one given via `kwargs`.
        """
        return type(self)(q=replace(self.q, **kwargs))

    def distinct(self, distinct: bool = True) -> Query:
        """Creates a new SQL statement with the distinct flag.

        Args:
            distinct (bool): True to only dump distinct rows, False otherwise.
            Defaults to True.

        Returns:
            Query: the new query
        """
        return self._mutate(distinct=distinct)

    def columns(self, *columns: Identifier) -> Query:
        """Creates a new SQL statement with given columns.

        Args:
            *columns (Identifier): list of columns.

        Returns:
            Query: the new query

        Examples:
            >>> # SELECT password FROM user
            >>> q = Query('user').columns('password')
            >>> # SELECT username,password,email FROM user
            >>> q = q.columns('username', 'password', 'email')
        """
        assert columns, "Cannot create a query with no columns"
        return self._mutate(columns=List[Identifier](columns))

    def table(self, table: Identifier) -> Query:
        """Creates a new SQL statement with given table.

        Args:
            table (Identifier): New table. If `None`, the statement will have no
            `FROM` clause.

        Returns:
            Query: the new query

        Examples:
            >>> # SELECT password FROM user
            >>> q = Query('user').columns('password')
            >>> # SELECT password FROM other_user_table
            >>> q.table('other_user_table')
        """
        return self._mutate(table=table)

    def where(self, condition: Expression, *args) -> Query:
        """Creates a new statement with given condition.

        Returns:
            Query: the new query

        Examples:
            >>> # SELECT password FROM user
            >>> q = Query('user').columns('password')
            >>> # SELECT password FROM user WHERE username LIKE 0x61646d696e25
            >>> q = q.where('username LIKE {}', 'admin%')
            >>> # SELECT password FROM user WHERE role_id=123
            >>> q.where('OR role_id={}', 123)
            >>> # SELECT password FROM user WHERE username=0x74657374
            >>> condition = (Identifier('username') == 'test')
            >>> q.where(condition)
        """
        if condition is not None and not isinstance(condition, Node):
            condition = Expression(condition, args)

        return self._mutate(where=condition)

    def order(
        self, order: str | List[Order] | tuple | list, reverse: bool = False
    ) -> Query:
        """Sets the order by clause of the query.

        Args:
            order (Order, List, tuple, list): Ordering column
            reverse (bool, optional): If true, the ordering is DESC.
                Defaults to False.

        Returns:
            Query: the instance

        Example:
            >>> # SELECT password FROM user
            >>> q = Query('user').columns('password')
            >>> # SELECT password FROM user ORDER BY id
            >>> q.order('id')
            >>> # SELECT password FROM user ORDER BY creation_date DESC, name ASC
            >>> q.order([
            ...     Order('creation_date', reverse=True),
            ...     Order('name')
            ... ])
        """
        if order is None:
            return self._mutate(order=None)

        match order:
            case List():
                assert not reverse, "Cannot set reverse on list of order fields"
                pass
            case tuple() | list():
                assert not reverse, "Cannot set reverse on list of order fields"
                order = List[Order](order)
            case Order():
                assert not reverse, "Cannot set reverse on order field of type Order"
                order = List[Order]((order,))
            case _:
                order = List[Order]((Order(order, reverse),))

        return self._mutate(order=order)

    def limit(self, start: int | Limit, rows: int = None, /) -> Query:
        """SQL `LIMIT` clause. Creates a new statement meant to return `rows`
        rows, starting from row `start`.

        Args:
            start (int): Row to start at. If None, the limit clause is omitted.
            rows (int, optional): Number of rows.

        Returns:
            Query: the instance

        Examples:
            >>> # SELECT password FROM users
            >>> q = Query('user').columns('password')
            >>> # SELECT password FROM users LIMIT 10,5
            >>> q.limit(10, 5)
            >>> # SELECT password FROM users LIMIT 0,3
            >>> q.limit(3)
            >>> # Remove limit:
            >>> # SELECT password FROM users
            >>> q.limit(None)
            >>> # SELECT password FROM users LIMIT 1,2
            >>> q.limit(Limit(1, 2))
        """
        match start:
            case Limit():
                limit = start
            case int() if rows is not None:
                limit = Limit(start, rows)
            case int() if rows is None:
                limit = Limit(0, start)
            case None:
                limit = None
            case _:
                raise TypeError(f"Invalid value for start: {start!r}")

        return self._mutate(limit=limit)

    def super_query(self) -> Query:
        """Returns a superquery that wraps this query.
        An alias for the table is generated. Columns are aliased if required.
        No other attribute (where, limit, order) are transfered from the
        subquery to the super query.

        Returns:
            Query: The superquery.

        Example:

            SELECT DISTINCT firstname, lastname, SUBSTR(pass,2) FROM users ->
            SELECT firstname, lastname, vJdg2 FROM (
                SELECT DISTINCT firstname, lastname, SUBSTR(pass,2) AS vJdg2
                FROM test.users
            ) AS vJdg
        """
        # TODO The downside ATM is that you lose column names for aliased
        # columns. In the example for instance, the result will yield vJdg2 as
        # the name of the 3rd column.
        alias = randomized.lower(4)

        #: Columns in the subquery
        sub_columns = []
        #: Columns in the superquery
        sup_columns = []

        for i, sub_column in enumerate(self.q.columns):
            # If a column is neither an identifier nor an alias, we need to
            # create an alias
            match sub_column:
                case Value():
                    # Add the value directly to the superquery
                    sup_columns.append(sub_column)
                    continue
                case Identifier():
                    # Add the value as-is to both
                    sup_column = sub_column
                case Alias():
                    # Add the alias to the superquery
                    sup_column = sub_column.alias
                case _:
                    # Create an alias for column and add it to the superquery
                    sub_column = Alias(sub_column, f"{alias}{i}")
                    sup_column = sub_column.alias

            sub_columns.append(sub_column)
            sup_columns.append(sup_column)

        sub_query = self.columns(*sub_columns)
        sup_table = Alias(sub_query, alias)
        return Query(sup_table).columns(*sup_columns)


@node
@dataclass(eq=False)
class Union(Node):
    """SQL `UNION` operator.

    Examples:

        >>> # SELECT NULL,NULL,version() FROM DUAL
        >>> query = Query([Value(None), Value(None), 'version()'], 'DUAL')
        >>> # role=123 UNION ALL SELECT NULL,NULL,version() FROM DUAL
        >>> union = Union(Identifier('role') == 123, query)
    """

    left: Query
    """Left side of the UNION"""
    right: Query
    """Right side of the UNION"""
    all: bool = False
    """Whether to use UNION (default) or UNION ALL"""
