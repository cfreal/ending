"""Collections of classes to test DBMSes."""

from copy import copy
from functools import partial
import itertools
import time
from abc import ABC, abstractmethod
from typing import Any

from ending.ast import *
from ending.db.generic.compiler import Compiler
from ending.db.generic.method import Method
from ending.util import logging, quoting
from ending.util.misc import to_bytes
from ending.util.typing import Table

__all__ = [
    "AstDB",
    "BaseDBTestCaseMixin",
    "CompilerTestCase",
    "MethodTestCase",
    "FormatTest",
]


class Vector:
    TABLE = 0
    ROW = 1
    CELL = 2

    def __init__(self, value, position: int):
        self.value = value
        assert 0 <= position <= 2
        self.position = position

    def as_cell(self):
        if self.position == self.TABLE:
            if len(self.value) != 1 or len(self.value[0]) != 1:
                raise DimensionException
            return self.value[0][0]
        if self.position == self.ROW:
            if len(self.value) != 1:
                raise DimensionException
            return self.value[0]
        return self.value


class DimensionException(Exception):
    """The obtained node has invalid dimensions."""


class AstDB:
    """A database that parses the AST and returns results."""

    def __init__(self, results: Table, debug: bool = False):
        self.results = results
        self.row_context = []
        self.aliases = []
        self.log = None

        if debug:
            self.log = logging.logger(__name__)
            self.log.setLevel("DEBUG")

    def query(self, query: Query) -> Table:
        return self.resolve(query, as_cell=False)

    def resolve(self, node: Node, as_cell: bool = True) -> Table:
        cls = type(node).__name__
        try:
            method = getattr(self, f"resolve_{cls}")
        except AttributeError:
            raise TypeError(f"Cannot resolve {cls}: {node!r}")

        if self.log:
            self.log.debug(f"Resolving {node!r} {self.row_context=}")

        try:
            resolved = method(node)
        except BaseException:
            if self.log:
                self.log.exception(f"Exception for {node!r} {self.row_context=}")
            raise

        if isinstance(resolved, Vector):
            if as_cell:
                return resolved.as_cell()
            return resolved.value

        return resolved

    def resolve_Identifier(self, node: Identifier):
        name = node.name
        try:
            alias = self.aliases[-1][name]
        except KeyError:
            pass
        else:
            return self.resolve(alias)
        idx = int(name.split("_")[1])

        try:
            return self.results[self.row_context[-1]][idx]
        except IndexError:
            raise ValueError(
                f"Column {idx} or row {self.row_context[-1]} is out of bounds"
            )

    def resolve_Not(self, not_: Not) -> bool:
        return not self.resolve(not_.node)

    def resolve_Hex(self, node: Hex):
        value = self.resolve(node.node)

        if isinstance(value, str):
            value = value.encode()
        assert isinstance(value, bytes)
        return value.hex().upper()

    def resolve_Case(self, case: Case):
        if case.condition:
            compare = self.resolve(case.condition)
        else:
            compare = True

        for test, result in case.cases:
            if self.resolve(test) == compare:
                return self.resolve(result)

        if case.default:
            return self.resolve(case.default)

        return None

    def resolve_Count(self, node: Count):
        return len(self.results)

    def resolve_Query(self, query: Query) -> Vector:
        # This probably will not work for super-super-queries, but for now this seems
        # enough
        self.aliases.append({})

        if isinstance(query.q.table, Alias):

            subquery: Query = query.q.table.node
            for column in subquery.q.columns:
                if isinstance(column, Alias):
                    self.aliases[-1][column.alias.name] = column.node

        if all(column.metadata.single for column in query.q.columns):
            self.row_context.append("NO_ROW_CONTEXT")
            rows = [[self.resolve(column) for column in query.q.columns]]
            self.row_context.pop()
        else:
            if query.q.limit:
                start_idx, stop_idx = query.q.limit.get_start_stop()
            else:
                start_idx, stop_idx = (0, len(self.results))

            rows = []

            for i in range(start_idx, stop_idx):
                if i >= len(self.results):
                    break
                self.row_context.append(i)
                rows.append([self.resolve(column) for column in query.q.columns])
                # There's probably better ways to do this
                if query.q.distinct:
                    rows = [list(row) for row in set(tuple(row) for row in rows)]
                self.row_context.pop()

        self.aliases.pop()

        return Vector(rows, Vector.TABLE)

    def resolve_IsNull(self, is_null: IsNull):
        node = self.resolve(is_null.node)
        return (node is None) ^ is_null.negate

    def resolve_ArithmeticOperation(self, arithmetic_operation: ArithmeticOperation):
        left = self.resolve(arithmetic_operation.left)
        right = self.resolve(arithmetic_operation.right)
        op = arithmetic_operation.operator

        if left is None or right is None:
            return None

        match op:
            case "+":
                return left + right
            case "-":
                return left - right
            case "*":
                return left * right
            case "/":
                return left / right
            case "%":
                return left % right
            case _:
                raise ValueError(f"Unknown operator {op!r}")

    def resolve_Length(self, length: Length):
        node = self.resolve(length.node)
        if node is None:
            return None
        return len(node)

    def to_bytes(self, value) -> bytes:
        """Returns a bytes representation of the value."""
        if isinstance(value, bool):
            return b"1" if value else b"0"
        if value is None:
            return b"NULL"
        return to_bytes(value)

    def resolve_ConcatWS(self, concat_ws: ConcatWS):
        separator = self.resolve(concat_ws.separator).encode()
        nodes = [self.resolve(node) for node in concat_ws.nodes]
        nodes = [self.to_bytes(node) for node in nodes]
        return separator.join(nodes)

    def resolve_Concatenation(self, concatenation: Concatenation):
        nodes = [self.resolve(node) for node in concatenation.nodes]
        nodes = [self.to_bytes(node) for node in nodes]
        return b"".join(nodes)

    def resolve_Value(self, value: Value):
        return value.value

    def resolve_Substring(self, substring: Substring):
        s = self.resolve(substring.string)
        if substring.start:
            s = s[substring.start :]
        if substring.length:
            s = s[: substring.length]
        return s

    def resolve_Ord(self, ord_: Ord):
        return ord(self.resolve(ord_.node))

    def resolve_List(self, list: List):
        return [self.resolve(item) for item in list.items]

    def resolve_IsIn(self, is_in: IsIn):
        left = self.resolve(is_in.needle)

        match is_in.haystack:
            case List(items):
                items = [item.value for item in items]
                result = left in items
            case other:
                raise NotImplementedError(f"IsIn with {type(other)} is not implemented")
        return result ^ is_in.negate

    def resolve_Function(self, function: Function):
        name = function.name.upper()
        if name == "COALESCE":
            value = self.resolve(function.args[0])
            if value is None:
                return self.resolve(function.args[1])
            return value
        if name == "SLEEP":
            value = self.resolve(function.args[0])
            time.sleep(value)
            return None

        raise NotImplementedError(f"Function {function.name} is not implemented")

    def resolve_LogicalOperation(self, logical_operation: LogicalOperation):
        op = logical_operation.operator
        assert op == "AND"

        left = self.resolve(logical_operation.left)

        if not left:
            return False

        right = self.resolve(logical_operation.right)

        return bool(right)

    def resolve_Comparison(self, comparison: Comparison):
        left = self.resolve(comparison.left)
        right = self.resolve(comparison.right)

        match comparison.operator:
            case "<":
                return left < right
            case "<=":
                return left <= right
            case ">":
                return left > right
            case ">=":
                return left >= right
            case "=":
                return left == right
            case "!=":
                return left != right
            case op:
                raise ValueError(f"Unknown operator {op!r}")

    def resolve_Cast(self, cast: Cast):
        node = self.resolve(cast.node)
        expr = cast.datatype.expression
        if expr in ("TEXT", "BLOB") or expr.startswith("VARCHAR"):
            if isinstance(node, bool):
                return b"1" if node else b"0"
            return to_bytes(node)
        raise NotImplementedError(
            f"Cast to {cast.datatype.expression} is not implemented"
        )

    def resolve_Expression(self, expression: Expression):
        match expression.expression:
            case "{} USING latin1":
                return self.resolve(expression.args[0])
            case _:
                raise NotImplementedError(
                    f"Expression {expression.expression} is not implemented"
                )


class CompilerTestCase(ABC):
    """A test case that tests the behavior of a compiler."""

    compiler_class: type[Compiler]
    compiler_args: dict[str, Any] = {"quote": quoting.singlequote}

    def setUp(self) -> None:
        self.compiler = self.get_compiler()

    async def asyncSetUp(self) -> None:
        self.setUp()

    def get_compiler(self, **kwargs) -> Compiler:
        kwargs = self.compiler_args | kwargs
        return self.compiler_class(**kwargs)

    def assertFormat(
        self,
        value: Node,
        expected: str = None,
        regex: str = None,
        formatter: str = "",
        serialized: bool = False,
    ) -> None:
        """Assert that the formatted value is equal to the expected value or matches the
        regex.
        """
        assert isinstance(value, Node), f"Not a node: {value!r}"
        if serialized:
            value = self.compiler.serialize(value)
        self.compiler.wrap(value)
        value = format(value, formatter)
        try:
            if regex is not None:
                self.assertRegex(value, regex)
            else:
                self.assertEqual(value, expected)
        except AssertionError as e:
            raise e from None


class FormatTest:
    """A test that checks if the given node really compiles into the expected string."""

    name: str
    node: Node
    expected: str | None
    regex: str | None
    formatter: str
    serialized: bool

    def __init__(
        self,
        node: Node,
        expected: str = None,
        regex: str = None,
        formatter: str = "",
        serialized: bool = False,
    ) -> None:
        assert isinstance(node, Node), f"Not a node: {node!r}"
        assert (
            expected is not None or regex is not None
        ), "Either expected or regex must be provided"
        assert (
            expected is None or regex is None
        ), "Only one of expected or regex can be provided"
        self.node = node
        self.expected = expected
        self.regex = regex
        self.formatter = formatter
        self.serialized = serialized

    def __set_name__(self, _, name: str) -> None:
        self.name = name

    def __get__(self, instance, _):
        if instance is None:
            return self
        return partial(self.__call__, instance)

    def __call__(self, test: CompilerTestCase) -> None:
        # If a test already exists with the same name, check that it tests against the
        # same node (to avoid accidentally overriding a test with another one)
        # TODO Add override option to allow overriding node
        previous = getattr(test.__class__.__base__, self.name, None)
        if previous is not None and isinstance(previous, FormatTest):
            if repr(previous.node) != repr(self.node):
                raise ValueError(
                    f"Test {self.name} already defined with a different node: {previous.node!r} vs {self.node!r}"
                )
        test.assertFormat(
            # Copy the node to avoid caching the compiler during compilation
            copy(self.node),
            expected=self.expected,
            regex=self.regex,
            formatter=self.formatter,
            serialized=self.serialized,
        )


class MethodTestCase(CompilerTestCase):
    """A test case that tests the behavior of a method."""

    db_class: type[AstDB] = AstDB
    method_class: type[Method]
    compiler_class: type[Compiler]
    method_args: dict[str, Any] = {}

    # Bounds contain a tuple with the minimal row number to inject, and the
    # nb of items to fetch
    bounds = [
        (None,),
        (0, 3),
        (0, 1),
        (4, 0),
        (20, 3),
    ]
    # Number of columns to query
    cols = [1, 2, 6]
    # Number of rows in the table (i.e. 0 table is empty)
    counts = [0, 1, 2, 10]

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.method = self.get_method()
        self.method.case = self

    def get_db(self, results) -> AstDB:
        return self.db_class(results)

    def get_method(self, **kwargs) -> Method:
        kwargs = (
            self.method_args
            | {"compiler": self.compiler, "inject": self.inject}
            | kwargs
        )
        return self.method_class(**kwargs)

    def get_query(self, cols, bounds):
        columns = [
            Identifier("col_0", type=TextType()),
            Identifier("col_1", type=TextType()),
            Identifier("col_2", type=IntType()),
            Identifier("col_3", type=BoolType()),
            Identifier("col_4", type=TextType()),
            Identifier("col_5", type=BlobType()),
        ]
        columns = columns[:cols]
        return Query("some_table").columns(*columns).limit(*bounds)

    async def inject(self, payload: Node) -> Table:
        return self.db.resolve(payload)

    async def verify_results(self, query: Query) -> None:
        try:
            real_results = self.db.query(query)
        except:
            self.fail(
                "Unable to generate real results from query, AstDB must be broken, test unreliable"
            )

        results = await self.method.fetch(query)
        self.assertEqual(results.data, real_results)

    async def test_run(self):
        for count, bounds, cols in itertools.product(
            self.counts, self.bounds, self.cols
        ):
            with self.subTest(count=count, bounds=bounds, cols=cols):
                self.db = self.get_db(
                    [
                        [f"string_col_{i}"]  # string col
                        + [None]  # null col
                        + [i * 10]  # integer col
                        + [i % 2 == 1]  # bool col
                        + ["123"]  # string col, static
                        + [f"blob_col_{i}".encode()]  # blob col, static
                        for i in range(count)
                    ]
                )
                query = self.get_query(cols, bounds)
                await self.verify_results(query)


class BaseDBTestCaseMixin:
    """A mixin to define the DB to use for the tests."""

    def get_db(self, results) -> AstDB:
        return super().get_db(results)

    @abstractmethod
    def get_compiler(self) -> Compiler:
        pass
