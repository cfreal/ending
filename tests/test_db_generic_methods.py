from __future__ import annotations

import itertools
import random
import re
import statistics
import string
import unittest
from abc import ABC
from asyncio import CancelledError
from copy import copy
from functools import partial
from unittest import IsolatedAsyncioTestCase, mock
from unittest.mock import AsyncMock

from ending.ast import *
from ending.db import generic
from ending.db.generic.method import (
    ByteSumTextCellFetcher,
    Method,
    RandomTag,
    TextCellFetcher,
)
from ending.exception import ConversionError, InjectionError
from ending.struct.metadata import (
    Context,
    Data,
    Nothing,
    QueryState,
    State,
    VoidContext,
)
from ending.struct.parameterized import ParameterError
from ending.util import polytomy, quoting
from ending.util.misc import to_bytes
from ending.util.typing import Table
from tests.test_db_generic_compiler import MockCompiler

from .db_testing import *


class TestAstDB(unittest.TestCase):
    def setUp(self) -> None:
        self.db = AstDB([[f"string_col_{i}"] + [None] + [i * 10] for i in range(5)])
        return super().setUp()

    def test_get_count(self):
        query = Query("users").columns(Count())
        results = self.db.query(query)
        self.assertEqual(results, [[5]])

    def test_get_all(self):
        query = Query("users").columns("col_0", "col_1", "col_2")
        results = self.db.query(query)
        self.assertEqual(results, self.db.results)


class SimpleRandomTag(RandomTag):
    def generate(self, obj: object) -> str:
        return str(random.choice((1, 2, 3)))


class SQLKeywordRandomTag(RandomTag):
    def __init__(self):
        self.keywords = iter(
            [
                "from",
                "join",
                "test",
            ]
        )
        super().__init__()

    def generate(self, obj: object) -> str:
        return next(self.keywords)


class TestRandomTag(IsolatedAsyncioTestCase):
    def test_random_tag_does_not_produce_identical_values(self):
        # The test is NOT perfectly determinist, but it should be good enough
        class X:
            x = SimpleRandomTag()
            y = SimpleRandomTag()
            z = SimpleRandomTag()

        x = X()
        self.assertEqual({x.x, x.y, x.z}, {"1", "2", "3"})
        x = X()
        self.assertEqual({x.x, x.y, x.z}, {"1", "2", "3"})

    def test_random_tag_does_not_yield_sql_keyword(self):
        class X:
            x = SQLKeywordRandomTag()

        x = X()
        self.assertEqual(x.x, "test")


class TestBaseMethod(IsolatedAsyncioTestCase):
    async def test_method_has_no_configurator(self):
        self.assertIsNone(Method.get_configurator())


# -- RowsMethod


class MockRowsMethod(generic.RowsMethod):
    async def fetch_rows(self, query: Query, ctx: Context):
        return self.case.db.query(query)


class MockBoundsMethod(generic.BoundedMethod):
    async def fetch_results_bounded(
        self, query: Query, bounds: tuple, ctx: Context
    ) -> Table:
        raise AssertionError(
            "fetch_results_bounded should not be called when bounds are restored"
        )


class MockCellMethod(generic.CellMethod):
    async def fetch_cell(self, query: Query, ctx: Context) -> Cell:
        raise AssertionError("fetch_cell should not be called when cell is restored")


class TestRowsMethod(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockRowsMethod
    method_args = {"nb_rows": 10}

    async def test_bounds_for_single_result_are_0_and_1(self):
        query = Query().columns(Value("A"))
        bounds = await self.method.fetch_bounds(query, ctx=VoidContext())
        self.assertEqual(bounds, (0, 1))

    async def test_get_count_on_distinct_returns_count_star_subquery(self):
        # SELECT DISTINCT a -> SELECT COUNT(*) FROM (SELECT DISTINCT a FROM b)
        query = Query("b").columns("a").distinct()
        count = self.method._sql_get_count(query)
        self.assertIsInstance(count.q.columns[0], Count)
        self.assertIsInstance(count.q.columns[0].column, Star)
        self.assertIsInstance(count.q.table, Alias)
        self.assertIsInstance(count.q.table.node, Query)
        self.assertFalse(count.q.columns[0].distinct)

    async def test_get_count_on_not_distinct_several_returns_count_star_of_query(self):
        # SELECT a1, a2 FROM b -> SELECT COUNT(*) FROM b
        query = Query("b").columns("a1", "a2")
        count = self.method._sql_get_count(query)
        self.assertIsInstance(count.q.columns[0], Count)
        self.assertIsInstance(count.q.columns[0].column, Star)
        self.assertIsInstance(count.q.table, Identifier)
        self.assertEqual(count.q.table.name, "b")
        self.assertFalse(count.q.columns[0].distinct)

    async def test_get_count_on_not_distinct_returns_count_star_of_query(self):
        # SELECT a FROM b -> SELECT COUNT(*) FROM b
        query = Query("b").columns("a")
        count = self.method._sql_get_count(query)
        self.assertIsInstance(count.q.columns[0], Count)
        self.assertIsInstance(count.q.columns[0].column, Star)
        self.assertIsInstance(count.q.table, Identifier)
        self.assertEqual(count.q.table.name, "b")
        self.assertFalse(count.q.columns[0].distinct)

    async def test_getint_raises_injectionerror_when_result_has_problem(self):
        with self.assertRaisesRegex(InjectionError, "MSG"):
            self.method._get_int([1], "MSG")
        with self.assertRaisesRegex(InjectionError, "MSG"):
            self.method._get_int([["a"]], "MSG")

    async def test_cancelled_gets_raised(self):
        self.method.fetch_rows = AsyncMock(side_effect=CancelledError("Boom!"))
        query = Query().columns(Value("A"))

        with self.assertRaises(CancelledError):
            await self.method.fetch(query)

    async def test_base_exception_gets_raised(self):
        class CustomException(Exception):
            pass

        self.method.fetch_rows = AsyncMock(side_effect=CustomException("Boom!"))
        query = Query().columns(Value("A"))

        with self.assertRaises(CustomException):
            await self.method.fetch(query)

    async def test_injecterror_gets_raised(self):
        self.method.fetch_rows = AsyncMock(side_effect=InjectionError("Boom!"))
        query = Query().columns(Value("A"))

        with self.assertRaises(InjectionError):
            await self.method.fetch(query)

    async def test_adjust_query_shields_from_unknown_columns(self):
        def _adjust_column(node):
            return node

        self.compiler._adjust_column = _adjust_column

        query = Query().columns(Identifier("a", type=UnknownType()))

        with self.assertRaises(ConversionError) as cm:
            await self.method.adjust_query(query, None)

        self.assertEqual(
            str(cm.exception),
            "One of the columns still has an unknown type [Payload: Query(q=QueryParts(table=None, columns=List[Identifier]((Identifier(name='a'),)), distinct=False, where=None, order=None, limit=None))]",
        )

    def test_default_get_validation(self):
        self.assertIsNone(self.method.get_validator())


class TestMethodRestore(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.compiler = MockCompiler(quote=quoting.singlequote)

    async def test_synced_fetch_bounds_uses_restore(self):
        class BoundState(State):
            def restore(self, key: str, context: Context):
                if key == "bounds":
                    return Data((1, 4))
                return Nothing

        method = MockBoundsMethod(self.compiler, None)
        bounds = await method._synced_fetch_bounds(
            Query().columns(Value("A")), ctx=Context(BoundState(), {})
        )
        self.assertEqual(bounds, (1, 4))

    async def test_synced_fetch_rows_uses_querystate_restore(self):
        state = QueryState()
        state.results = {0: {0: "a"}, 1: {0: "b"}}
        query = Query("t").columns(Identifier("col_0", type=TextType())).limit(0, 2)
        state.query = query
        method = MockRowsMethod(self.compiler, None, nb_rows=10)
        await method.setup_semaphores()

        rows = await method._synced_fetch_rows(
            query, ctx=Context(state, {"row": 0, "nb_rows": 2})
        )
        self.assertEqual(rows, [["a"], ["b"]])

    async def test_synced_fetch_cell_uses_querystate_restore(self):
        state = QueryState()
        state.results = {0: {0: "a"}}
        query = Query("t").columns(Identifier("col_0", type=TextType())).limit(0, 1)
        method = MockCellMethod(self.compiler, None)
        await method.setup_semaphores()

        cell = await method._synced_fetch_cell(
            query, ctx=Context(state, {"row": 0, "column": 0})
        )
        self.assertEqual(cell, "a")


class MockRowsMethodWithResultsThriceABABAB(generic.RowsMethod):
    async def fetch_rows(self, query: Query, ctx: Context):
        results = self.case.db.query(query)
        return results + results + results


class TestRowsMethodWithResultsThriceABABAB(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockRowsMethodWithResultsThriceABABAB
    method_args = {"nb_rows": 10}


class MockRowsMethodWithResultsThriceAAABBB(generic.RowsMethod):
    async def fetch_rows(self, query: Query, ctx: Context):
        results = self.case.db.query(query)
        return sum(([x, x, x] for x in results), [])


class TestRowsMethodWithResultsThriceAAABBB(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockRowsMethodWithResultsThriceAAABBB
    method_args = {"nb_rows": 10}


# -- HexDisplayMethod


class MockDisplayMethod(generic.DisplayMethod):
    def get_validator(self):
        return "Not none. This is a mock value for TestHexDisplayMethod.test_get_validation_returns_validation_object_if_hex_is_false"

    def fetch_merged_rows(self, query: Query, ctx: Context) -> Table:
        raise AssertionError("fetch_merged_rows should not be called while testing")


class MockHexDisplayMethod(generic.HexDisplayMethod, MockDisplayMethod, ABC):
    pass


class MockHexDisplayMethodWithHex(MockHexDisplayMethod):
    tag_null: str = "n"


class MockHexDisplayMethodWithoutHex(MockHexDisplayMethod):
    pass


class SerializeCellTest:
    """A test that checks if the given node really converts into the expected string."""

    name: str
    node: Node
    expected: str | None
    regex: str | None

    def __init__(
        self,
        node: Node,
        expected: str = None,
        regex: str = None,
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
        if previous is not None and isinstance(previous, SerializeCellTest):
            if repr(previous.node) != repr(self.node):
                raise ValueError(
                    f"Test {self.name} already defined with a different node: {previous.node!r} vs {self.node!r}"
                )
        test.assertFormatSerialized(
            # Copy the node to avoid caching the compiler during compilation
            copy(self.node),
            expected=self.expected,
            regex=self.regex,
        )


class TestHexDisplayMethodWithHex(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    compiler_args = {"quote": quoting.hexadecimal}
    method_class = MockHexDisplayMethodWithHex
    method_args = {"nb_rows": 1, "hex": True}
    counts = []
    """Do not run any "normal" tests, as the point of this test class is to test the hex
    display mixin, not the method itself.
    """

    def test_does_not_split_tags(self):
        self.assertFalse(self.method._split_tags)

    def test_serialize_cell_propagates_single(self):
        serialized = self.method.serialize_cell(Value("a"))
        self.assertTrue(serialized.metadata.single)
        serialized = self.method.serialize_cell(Identifier("a", type=BlobType()))
        self.assertFalse(serialized.metadata.single)

    test_serialize_cell_for_text = SerializeCellTest(
        Identifier("a", type=TextType()),
        "COALESCE(HEX(a),0x6e)",
    )
    test_serialize_cell_for_text_not_nullable = SerializeCellTest(
        Identifier("a", type=TextType(), nullable=False),
        "HEX(a)",
    )
    test_serialize_cell_for_blob = SerializeCellTest(
        Identifier("a", type=BlobType()),
        "COALESCE(HEX(a),0x6e)",
    )
    test_serialize_cell_for_blob_not_nullable = SerializeCellTest(
        Identifier("a", type=BlobType(), nullable=False),
        "HEX(a)",
    )
    test_serialize_cell_for_int = SerializeCellTest(
        Identifier("a", type=IntType()),
        "COALESCE(CAST(a AS TEXT),0x6e)",
    )
    test_serialize_cell_for_int_not_nullable = SerializeCellTest(
        Identifier("a", type=IntType(), nullable=False),
        "CAST(a AS TEXT)",
    )
    test_serialize_cell_for_bool = SerializeCellTest(
        Identifier("a", type=BoolType()),
        "COALESCE(CAST(a AS TEXT),0x6e)",
    )
    test_serialize_cell_for_bool_not_nullable = SerializeCellTest(
        Identifier("a", type=BoolType(), nullable=False),
        "CAST(a AS TEXT)",
    )

    def assertFormatSerialized(
        self,
        value: Node,
        expected: str = None,
        regex: str = None,
    ) -> None:
        """Assert that the formatted value is equal to the expected value or matches the
        regex.
        When serialized is True, the value is first serialized with the method's
        serialize_cell method, instead of the compiler's serialize method.
        """
        assert isinstance(value, Node), f"Not a node: {value!r}"
        value = self.method.serialize_cell(value)
        self.compiler.wrap(value)
        value = format(value)
        try:
            if regex is not None:
                self.assertRegex(value, regex)
            else:
                self.assertEqual(value, expected)
        except AssertionError as e:
            raise e from None

    def test_deserialize_cell_for_text(self):
        id = Identifier("a", type=TextType())
        self.assertEqual(self.method.deserialize_cell(id, b"414243"), "ABC")

    def test_deserialize_cell_for_blob(self):
        id = Identifier("a", type=BlobType())
        self.assertEqual(self.method.deserialize_cell(id, b"414243"), b"ABC")

    def test_deserialize_cell_for_int(self):
        id = Identifier("a", type=IntType())
        self.assertEqual(self.method.deserialize_cell(id, b"414243"), 414243)

    def test_deserialize_cell_for_bool(self):
        id = Identifier("a", type=BoolType())
        self.assertEqual(self.method.deserialize_cell(id, b"1"), True)
        self.assertEqual(self.method.deserialize_cell(id, b"0"), False)

    def test_deserialize_cell_raises_exception_if_data_is_not_hex(self):
        id = Identifier("a", type=TextType())
        with self.assertRaises(InjectionError) as cm:
            self.method.deserialize_cell(id, b"NOTHEX")
        self.assertEqual(cm.exception.args[0], "Unable to decode hex chunk: 'NOTHEX'")

    def test_deserialize_cell_raises_exception_if_string_is_not_utf8(self):
        id = Identifier("a", type=TextType())
        with self.assertRaises(InjectionError) as cm:
            self.method.deserialize_cell(id, b"FFFF")
        self.assertEqual(
            cm.exception.args[0],
            r"Unable to convert to string: b'\xff\xff' (try retrieving as blob)",
        )

    def test_deserialize_cell_with_none_does_not_raise_typeerror(self):
        id = Identifier("a", type=TextType())
        self.assertEqual(
            self.method.deserialize_cell(id, self.method.tag_null.encode()), None
        )

    def test_random_tag_is_single_char_not_hex_if_hex_is_true(self):
        method = self.get_method(hex=True)
        self.assertEqual(len(method.tag_start), 4)
        self.assertEqual(len(method.tag_stop), 1)
        self.assertEqual(len(method.tag_separator), 1)
        self.assertEqual(len(method.tag_null), 1)
        self.assertNotIn(method.tag_stop, string.hexdigits)
        self.assertNotIn(method.tag_separator, string.hexdigits)
        self.assertNotIn(method.tag_null, string.hexdigits)

    def test_get_validation_does_not_return_none_if_hex_is_true(self):
        self.assertIsNotNone(self.method.get_validator())

    def test_split_tags_of_one_element_returns_one_tag_only(self):
        assert not self.method._split_tags, "test is not valid if split_tags is true"
        split = self.method._maybe_split_tag("a")
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0].value, "a")

    def test_split_tags_of_several_elements_returns_one_tag_only(self):
        assert not self.method._split_tags, "test is not valid if split_tags is true"

        split = self.method._maybe_split_tag("abc")
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0].value, "abc")


class TestHexDisplayMethodWithoutHex(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    compiler_args = {"quote": quoting.hexadecimal}
    method_class = MockHexDisplayMethodWithoutHex
    method_args = {"nb_rows": 1, "hex": False}
    counts = []
    """Do not run any "normal" tests, as the point of this test class is to test the hex
    display mixin, not the method itself.
    """

    def test_random_tag_is_not_single_char_if_hex_is_false(self):
        method = self.get_method(hex=False)
        self.assertEqual(len(method.tag_start), 4)
        self.assertEqual(len(method.tag_stop), 4)
        self.assertEqual(len(method.tag_separator), 4)

    def test_get_validation_returns_validation_object_if_hex_is_false(self):
        self.assertIsNotNone(self.method.get_validator())

    def test_split_tags_of_one_element_returns_one_tag_only(self):
        compiler = self.get_compiler(quote=quoting.singlequote)
        method = self.get_method(compiler=compiler)
        assert method._split_tags, "test is not valid if split_tags is false"
        split = method._maybe_split_tag("a")
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0].value, "a")

    def test_split_tags_of_several_elements_returns_several_tags(self):
        compiler = self.get_compiler(quote=quoting.singlequote)
        method = self.get_method(compiler=compiler)
        assert method._split_tags, "test is not valid if split_tags is false"
        split = method._maybe_split_tag("abc")
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0].value, "a")
        self.assertEqual(split[1].value, "bc")


# -- RowMethod


class MockRowMethod(generic.RowMethod):
    async def fetch_row(self, query: Query, ctx: Context):
        return self.case.db.query(query)[0]


class TestRowMethod(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockRowMethod

    async def test_cannot_change_nb_row(self):
        with self.assertRaisesRegex(
            TypeError, r"__init__\(\) got an unexpected keyword argument 'nb_rows'"
        ):
            MockRowMethod(compiler=self.compiler, inject=self.inject, nb_rows=2)


# -- ChunkMethod


class MockChunkMethodWithPattern(generic.ChunkMethod):
    def __init__(self, compiler, inject):
        super().__init__(compiler, inject, size=30, pattern=b"Data: (.*)")


class TestChunkMethodWithPattern(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockChunkMethodWithPattern

    async def inject(self, payload: Node):
        data = await super().inject(payload)
        return b"Data: " + data

    async def test_extract_results_does_not_find_pattern_gives_exception(self):
        with self.assertRaisesRegex(InjectionError, "Unable to find chunk"):
            self.method.extract_results(b"nothing of interest", payload=None)

    async def test_fetch_row_does_not_find_pattern_gives_exception(self):
        async def inject(payload):
            return b"nothing of interest"

        method = MockChunkMethodWithPattern(compiler=self.compiler, inject=inject)

        with self.assertRaisesRegex(InjectionError, "Unable to find chunk") as ctx:
            await method.fetch_merged_rows(
                Query("users").columns("col_1").limit(0, 1), ctx=VoidContext()
            )

        self.assertIsNotNone(ctx.exception.payload)

    async def test_fetch_row_with_empty_pattern_gives_exception(self):
        async def inject(payload):
            return b"Data: "

        method = MockChunkMethodWithPattern(compiler=self.compiler, inject=inject)

        with self.assertRaisesRegex(InjectionError, "Retrieved chunk is empty") as ctx:
            await method.fetch_merged_rows(
                Query("users").columns("col_1").limit(0, 1), ctx=VoidContext()
            )

        self.assertIsNotNone(ctx.exception.payload)


class TestChunkMethodWithPatternWithUpperCasedResults(
    MethodTestCase, IsolatedAsyncioTestCase
):
    compiler_class = MockCompiler
    method_class = MockChunkMethodWithPattern

    async def inject(self, payload: Node):
        data = await super().inject(payload)
        return b"Data: " + data.upper()

    async def verify_results(self, query: Query) -> None:
        try:
            real_results = self.db.query(query)
        except:
            self.fail(
                "Unable to generate real results from query, AstDB must be broken, test unreliable"
            )

        results = await self.method.fetch(query)
        self.assertEqual(
            [
                [x.lower() if isinstance(x, (bytes, str)) else x for x in row]
                for row in results.data
            ],
            real_results,
        )


class MockChunkMethodWithoutPattern(generic.ChunkMethod):
    def __init__(self, compiler, inject):
        super().__init__(
            compiler,
            inject,
            size=30,
        )


class TestChunkMethodWithoutPattern(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = MockChunkMethodWithoutPattern

    async def inject(self, payload: Node):
        data = await super().inject(payload)
        return b"Data: " + data

    async def test_no_pattern_and_no_start_tag_raises_exception(self):
        class FakeChunkMethod(generic.ChunkMethod):
            tag_start = None

        with self.assertRaisesRegex(
            ValueError, "If pattern is not set, tag_start must be"
        ):
            FakeChunkMethod(self.compiler, self.inject)


# -- SelectMethod


class TestSelectMethod(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = generic.SelectMethod
    method_args = dict(columns=4, column=1, nb_rows=5)

    async def test_split_tags_works(self):
        self.compiler.quote = quoting.singlequote
        self.assertTrue(self.method._should_split_tags())
        self.compiler.quote = quoting.hexadecimal
        self.assertFalse(self.method._should_split_tags())

    async def inject(self, payload: Node):
        results = self.db.query(payload)
        results = b"".join(to_bytes(cell, b"None") for row in results for cell in row)
        return b"Data: " + results

    async def test_columns_as_list_are_cast_to_values(self):
        method = self.get_method(columns=[Expr("A"), 1, None])
        self.assertEqual(len(method.columns), 3)
        self.assertIsInstance(method.columns[0], Expression)
        self.assertEqual(method.columns[0].expression, "A")
        self.assertIsInstance(method.columns[1], Value)
        self.assertEqual(method.columns[1].value, 1)
        self.assertIsInstance(method.columns[2], Value)
        self.assertEqual(method.columns[2].value, None)

    async def test_extract_results_does_not_find_pattern_raises_exception(self):
        with self.assertRaisesRegex(InjectionError, "Unable to find chunk"):
            self.method.extract_results(b"nothing of interest", payload=None)

    async def fetch_two_rows(self, payload: Node):
        results = self.db.query(payload)
        results = b"".join(
            to_bytes(cell, b"None")
            for i, row in enumerate(results)
            for cell in row
            if i < 2
        )
        return b"Data: " + results

    async def test_method_returns_fewer_rows_than_expected(self):
        self.db = self.get_db(
            [
                [f"string_col_{i}"]  # string col
                + [None]  # null col
                + [i * 10]  # integer col
                + [i % 2 == 1]  # bool col
                + ["123"]  # string col, static
                + [f"blob_col_{i}".encode()]  # blob col
                for i in range(10)
            ]
        )
        method = self.get_method(inject=self.fetch_two_rows, columns=3, nb_rows=5)

        with self.assertRaisesRegex(
            InjectionError, "Task #0 dumped 2 rows instead of the expected 5"
        ):
            await method.fetch(self.get_query(3, (0, 10)))

    def test_method_sets_split_tags_if_the_pattern_matches_the_compiled_values(self):
        compiler = MockCompiler(quote=quoting.singlequote)

        class MockSelectMethod(self.method_class):
            tag_start = "ABCD"

        method = MockSelectMethod(
            compiler=compiler,
            inject=self.fetch_two_rows,
            columns=3,
            column=1,
            nb_rows=5,
        )
        self.assertIs(method._split_tags, True)

    def test_method_does_not_set_split_tags_if_the_pattern_does_not_match_the_compiled_values(
        self,
    ):
        compiler = MockCompiler(quote=quoting.hexadecimal)

        class MockSelectMethod(self.method_class):
            tag_start = "ABCD"

        method = MockSelectMethod(
            compiler=compiler,
            inject=self.fetch_two_rows,
            columns=3,
            column=1,
            nb_rows=5,
        )
        self.assertIs(method._split_tags, False)

    def test_column_not_in_range_for_columns_raises_parametererror(self):
        with self.assertRaisesRegex(
            ParameterError,
            "parameter 'column' is invalid: index 5 is out of range for 3 columns",
        ):
            self.get_method(
                columns=3,
                column=5,
                nb_rows=5,
            )


# -- TestMethod


def build_all_test_methods_variations(_globals, db, module):
    # _globals = globals()
    compiler = getattr(module, "Compiler")
    method = getattr(module, "TestMethod")

    classes = [
        TestTestMethod,
        TestTestMethodWithResilience,
        TestTestMethodWithOKCharset,
        TestTestMethodWithInsufficientCharsetAndNoWildcardForText,
        TestTestMethodWithInsufficientCharsetAndSomeWildcardForText,
        TestTestMethodWithInsufficientCharsetAndNoWildcardForBlob,
        TestTestMethodWithInsufficientCharsetAndSomeWildcardForBlob,
    ]

    for cls in classes:

        class DBMSVariation(cls):
            db_class = db
            compiler_class = compiler
            method_class = method

        _globals[cls.__name__] = DBMSVariation


class TestTestMethodNumberStats(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.compiler = MockCompiler(quote=quoting.hexadecimal)
        self.method = generic.TestMethod(
            compiler=self.compiler, inject=self.sql_test_number
        )
        await super().asyncSetUp()

    async def sql_test_number(self, expr):
        assert isinstance(expr, Comparison)

        left = expr.left.value
        right = expr.right.value

        match expr.operator:
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

    async def test_number_range(self):
        number_ranges = [
            (1, 0, 100),
            (1, -100, 100),
            (0, 0, 100),
            (100, 0, 100),
            (-70, -100, -50),
        ]

        for n, smin, smax in number_ranges:
            expr = Value(n)
            r = await self.method.fetcher_int.fetch_from_range(expr, smin, smax)
            self.assertEqual(n, r)

        minus_one = Value(-1)
        r = await self.method.fetcher_int.fetch_from_range(minus_one, 0, 10)
        self.assertNotEqual(-1, r)

    async def test_number_range_stats(self):
        cases = [
            # Standard: not clustered case
            (10, [10, 100, 20, 50, -10], -20, 100),
            # Standard: clustered
            (10, [0, 1, 2, 3, 4, 5], -1000, 1000),
            # Standard: clustered
            (3, [1, 2, 3, 4, 5], -1000, 1000),
            # Standard: right == max, skip test
            (4, [0, 1, 2, 3, 4, 4], -1000, 4),
            # Standard: left == min, skip test
            (0, [0, 0, 2, 3, 4, 5], 0, 1000),
            # Edge: clustered case, outlier
            (12, [1000, 1002, 1009, 1008, 1007, 1007, 1010, 1011, 1014], 0, 1015),
            # Edge: clustered case, outlier
            (0, [10, 12, 9, 8, 7, 7, 10, 11, 14], 0, 100),
            # Hash: everything has the same length
            (32, [32, 32, 32, 32, 32], 0, 64),
            # Edge: everything is empty
            (0, [0, 0, 0, 0, 0], 0, 100),
            # Edge: everything was empty before
            (100, [0, 0, 0, 0, 0], 0, 100),
            # Standard: negative value amongst random values
            (-100, [10, 1000, -123, -487, 19], -1200, 1200),
            # Standard: negative but previous values are positive
            (-100, [10, 1000, 123, 487, 19], -1200, 1200),
        ]

        for number, pvalues, emin, emax in cases:
            with self.subTest(
                "number_range_stats",
                number=number,
                pvalues=pvalues,
                emin=emin,
                emax=emax,
            ):
                assert emin <= number <= emax
                assert all(emin <= pvalue <= emax for pvalue in pvalues)

                table = itertools.product([emin, None], [emax, None])

                for tmin, tmax in table:
                    with self.subTest(
                        "with, without bounds",
                    ):
                        ntype = IntType(min=tmin, max=tmax)
                        for v in pvalues:
                            ntype.feedback(v)
                        expr = Value(number, type=ntype)
                        smin, smax = await self.method.fetcher_int.fetch_range_stats(
                            expr
                        )

                        if tmin is not None:
                            self.assertGreaterEqual(smin, tmin)
                        if tmax is not None:
                            self.assertLessEqual(smax, tmax)

                        if smin is not None:
                            self.assertLessEqual(smin, number)
                        if smax is not None:
                            self.assertGreaterEqual(smax, number)

    async def test_number_range_stats_stddev_ok(self):
        ntype = IntType()
        for _ in range(10):
            ntype.feedback(4)

        assert statistics.mean(ntype.previous_values) == 4
        assert statistics.stdev(ntype.previous_values) == 0

        expr = Value(4, type=ntype)
        smin, smax = await self.method.fetcher_int.fetch_range_stats(expr)
        self.assertEqual(self.method.nb_requests, 1)
        self.assertEqual(smin, 4)
        self.assertEqual(smax, 4)

    async def test_number_range_stats_stddev_not_ok(self):
        ntype = IntType()
        for _ in range(10):
            ntype.feedback(4)

        assert statistics.mean(ntype.previous_values) == 4
        assert statistics.stdev(ntype.previous_values) == 0

        expr = Value(5, type=ntype)
        smin, smax = await self.method.fetcher_int.fetch_range_stats(expr)
        self.assertEqual(self.method.nb_requests, 1)
        self.assertEqual(smin, None)
        self.assertEqual(smax, None)

    async def test_number_range_stats_min_is_max(self):
        ntype = IntType(min=5, max=5)
        expr = Expr("{}", 5, type=ntype)
        smin, smax = await self.method.fetcher_int.fetch_range_stats(expr)
        self.assertEqual(self.method.nb_requests, 0)
        self.assertEqual((smin, smax), (5, 5))


class TestTestMethod(MethodTestCase, IsolatedAsyncioTestCase):
    compiler_class = MockCompiler
    method_class = generic.TestMethod

    async def test_compute_nb_sections(self):
        query = Query().columns(Value("A"))
        self.assertEqual(self.method.compute_nb_sections(query), 2)

    async def test_polytomy_raises_PolytomyNotInSetError(self):
        real_run = polytomy.run

        async def run(*args, **kwargs):
            run.i += 1
            if run.i < 10:
                raise polytomy.PolytomyNotInSetError()
            return await real_run(*args, **kwargs)

        run.i = 0

        self.db = self.get_db([[]])

        method = self.get_method()
        expr = Query().columns(Value("B", type=TextType(charset="ABC")))

        if method.wildcard is None:
            with (
                mock.patch.object(polytomy, "run", run),
                self.assertRaises(InjectionError) as cm,
            ):
                await method.fetch(expr)

            self.assertIn("Character is not in charset", str(cm.exception))
        else:
            with mock.patch.object(polytomy, "run", run):
                results = await method.fetch(expr)

            self.assertEqual(results.data, [[self.method.wildcard]])

    async def test_polytomy_raises_PolytomyNotSingletonError(self):
        real_run = polytomy.run

        async def run(*args, **kwargs):
            run.i += 1
            if run.i < 10:
                raise polytomy.PolytomyNotSingletonError()
            return await real_run(*args, **kwargs)

        run.i = 0

        self.db = self.get_db([[]])
        method = self.get_method()

        expr = Query().columns(Value("B", type=TextType(charset="ABC")))

        with (
            mock.patch.object(polytomy, "run", run),
            self.assertRaises(polytomy.PolytomyNotSingletonError) as cm,
        ):
            await method.fetch(expr)

    async def test_text_candidates_are_ord(self):
        fetcher = TextCellFetcher(self.method)
        expr = Expr("a", type=TextType("Aéî"))
        self.assertEqual(await fetcher.get_candidates(expr), (0x41, 233, 238))

    async def test_byte_sum_text_candidates_are_bytesums(self):
        fetcher = ByteSumTextCellFetcher(self.method)
        expr = Expr("a", type=TextType("Aéî"))
        self.assertEqual(await fetcher.get_candidates(expr), (0x41, 50089, 50094))

    async def test_wildcard_can_be_given_as_string(self):
        method = self.get_method(
            wildcard="?",
        )
        self.assertEqual(method.wildcard, "?")

    async def test_wildcard_cannot_be_given_as_not_string(self):
        with self.assertRaisesRegex(
            ParameterError,
            "parameter 'wildcard' is invalid: type must be str, not bytes",
        ):
            self.get_method(wildcard=b"?")

    async def test_fetch_int_with_unknown_very_low_min_bound(self):
        self.db = self.get_db([[]])
        value = -10000
        await self.method.setup_semaphores()
        result = await self.method.fetch_int(
            Value(value, type=IntType(max=0)), ctx=VoidContext()
        )
        self.assertEqual(result, value)

    async def test_fetch_int_with_unknown_very_high_max_bound(self):
        self.db = self.get_db([[]])
        value = 10000
        await self.method.setup_semaphores()
        result = await self.method.fetch_int(
            Value(value, type=IntType(min=0)), ctx=VoidContext()
        )
        self.assertEqual(result, value)

    async def test_fetch_cell_for_unknowntype_raises(self):
        self.db = self.get_db([[]])
        await self.method.setup_semaphores()
        with self.assertRaises(RuntimeError) as cm:
            await self.method.fetch_cell(
                Query().columns(Value("a", type=UnknownType())),
                VoidContext(),
            )
        self.assertEqual(
            str(cm.exception),
            "Cannot fetch cell Value(value='a') with type UnknownType()",
        )

    async def test_fetch_blob_comparison(self):
        self.db = self.get_db([[]])
        method = self.get_method()
        await method.setup_semaphores()
        result = await method.fetch_bool(Value("A") == Value("A"), VoidContext())
        self.assertIs(result, True)

    async def test_fetch_blob_other(self):
        self.db = self.get_db([[]])
        method = self.get_method()
        await method.setup_semaphores()
        result = await method.fetch_bool(Value(True), VoidContext())
        self.assertIs(result, True)


class TestTestMethodWithResilience(TestTestMethod):
    method_args = {"resilient": True}

    async def test_polytomy_raises_PolytomyNotInSetError(self):
        real_run = polytomy.run

        async def run(*args, **kwargs):
            run.i += 1
            if run.i < 10:
                raise polytomy.PolytomyNotInSetError()
            return await real_run(*args, **kwargs)

        run.i = 0

        self.db = self.get_db([[]])
        method = self.get_method(resilient=True)

        expr = Query().columns(Value("B", type=TextType(charset="ABC")))

        with mock.patch.object(polytomy, "run", run):
            results = await method.fetch(expr)

        self.assertEqual(results.data, [["B"]])

    async def test_polytomy_raises_PolytomyNotSingletonError(self):
        real_run = polytomy.run

        async def run(*args, **kwargs):
            run.i += 1
            if run.i < 10:
                raise polytomy.PolytomyNotSingletonError()
            return await real_run(*args, **kwargs)

        run.i = 0

        self.db = self.get_db([[]])
        method = self.get_method(resilient=True)

        expr = Query().columns(Value("B", type=TextType(charset="ABC")))

        with mock.patch.object(polytomy, "run", run):
            results = await method.fetch(expr)

        self.assertEqual(results.data, [["B"]])


class TestTestMethodWithOKCharset(TestTestMethod):
    def get_query(self, cols, bounds):
        query = super().get_query(cols, bounds)
        columns = query.q.columns
        columns = (columns[0].with_type(TextType(charset=string.printable)),) + columns[
            1:
        ]
        return query.columns(*columns)


class TestTestMethodWithInsufficientCharsetAndNoWildcardForText(TestTestMethod):
    """Verifies that if we fetch a column, and one of its letters it not in the
    charset, but we have a wildcard, we don't get an exception.
    """

    counts = [10]
    cols = [1]
    bounds = [(0, 1)]
    method_args = {}

    def get_query(self, cols, bounds):
        return (
            Query("some_table")
            .columns(Identifier("col_0", type=TextType(charset="tex")))
            .limit(*bounds)
        )

    async def verify_results(self, query: Query) -> None:
        self.db = self.get_db([[f"text_col_0"]])
        with self.assertRaisesRegex(InjectionError, "Character is not in charset"):
            r = await self.method.fetch(query)


class TestTestMethodWithInsufficientCharsetAndSomeWildcardForText(TestTestMethod):
    """Verifies that if we fetch a column, and one of its letters it not in the
    charset, but we have a wildcard, we don't get an exception.
    """

    counts = [10]
    cols = [1]
    bounds = [(0, 1)]
    method_args = {"wildcard": "?"}

    def get_query(self, cols, bounds):
        return (
            Query("some_table")
            .columns(Identifier("col_0", type=TextType(charset="tex")))
            .limit(*bounds)
        )

    async def verify_results(self, query: Query) -> None:
        self.db = self.get_db([[f"text_col_0"]])
        results = await self.method.fetch(query)
        self.assertEqual(results.data, [["text??????"]])


class TestTestMethodWithInsufficientCharsetAndNoWildcardForBlob(TestTestMethod):
    """Verifies that if we fetch a column, and one of its letters it not in the
    charset, but we have a wildcard, we don't get an exception.
    """

    counts = [10]
    cols = [1]
    bounds = [(0, 1)]
    method_args = {}

    def get_query(self, cols, bounds):
        return (
            Query("some_table")
            .columns(Identifier("col_0", type=BlobType(byteset=b"blo")))
            .limit(*bounds)
        )

    async def verify_results(self, query: Query) -> None:
        self.db = self.get_db([[b"blob_col_0"]])
        with self.assertRaisesRegex(InjectionError, "Byte is not in byteset"):
            r = await self.method.fetch(query)


class TestTestMethodWithInsufficientCharsetAndSomeWildcardForBlob(TestTestMethod):
    """Verifies that if we fetch a column, and one of its letters it not in the
    charset, but we have a wildcard, we don't get an exception.
    """

    counts = [10]
    cols = [1]
    bounds = [(0, 1)]
    method_args = {"wildcard": "?"}

    def get_query(self, cols, bounds):
        return (
            Query("some_table")
            .columns(Identifier("col_0", type=BlobType(byteset=b"blo")))
            .limit(*bounds)
        )

    async def verify_results(self, query: Query) -> None:
        self.db = self.get_db([[b"blob_col_0"]])
        results = await self.method.fetch(query)
        self.assertEqual(results.data, [[b"blob??ol??"]])


class TestTimebasedTestMethod(TestTestMethodWithResilience):
    method_class = generic.TimebasedTestMethod
    method_args = {"delay": 0.2}

    # Bounds contain a tuple with the minimal row number to inject, and the
    # nb of items to fetch
    bounds = [
        (None,),
        (1, 1),
    ]
    # Number of columns to query
    cols = [1]
    # Number of rows in the table (i.e. 0 table is empty)
    counts = [2]

    async def inject(self, payload: Node):
        payload = payload & Function["SLEEP"](0.2)
        return await super().inject(payload)

    async def test_compute_nb_sections(self):
        query = Query().columns(Expr("A", type=TextType(charset="A")))
        self.assertEqual(self.method.compute_nb_sections(query), 2)

        query = Query().columns(Expr("A", type=BlobType(byteset=b"A")))
        self.assertEqual(self.method.compute_nb_sections(query), 2)


class TestFetchers(unittest.IsolatedAsyncioTestCase):
    def test_composite_fetcher_list_cache_works(self):
        fetcher = TextCellFetcher(generic.TestMethod)
        self.assertEqual(
            fetcher._get_list_of_candidates(frozenset({1, 2, 3})),
            fetcher._get_list_of_candidates(frozenset({1, 2, 3})),
        )
