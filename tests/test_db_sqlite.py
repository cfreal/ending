import re
import unittest

from ending.ast import *
from ending.ast import Ord, Query
from ending.db import generic, sqlite
from ending.db.generic.compiler import Compiler
from ending.db.generic.method import InjectForBytes
from ending.struct.metadata import Context
from ending.util import quoting
from ending.util.typing import Table
from tests import test_db_generic_methods
from tests.test_db_generic_methods import SerializeCellTest
from tests.test_db_generic_compiler import FormatTest
from tests import test_db_generic_compiler
from tests.db_testing import AstDB

from .db_testing import AstDB


class TestCompiler(test_db_generic_compiler.TestCompiler):
    compiler_class = sqlite.Compiler

    test_substring = FormatTest(
        Substring("x", 11, 3),
        "SUBSTR(x,12,3)",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "SUBSTR(x,2)",
    )

    test_ord_texttype = FormatTest(
        Ord(Identifier("x", type=TextType())),
        "unicode(x)",
    )

    def test_ord_blobtype(self):
        with self.assertRaises(ValueError):
            self.assertFormat(Ord(Identifier("x", type=BlobType())), "this won't work")

    test_length_texttype = FormatTest(
        Length(Identifier("OP1", type=TextType())),
        "LENGTH(OP1)",
    )
    test_length_blobtype = FormatTest(
        Length(Identifier("OP1", type=BlobType())),
        "LENGTH(OP1)",
    )
    test_concatenation_no_args = FormatTest(
        Concatenation([]),
        "''",
    )
    test_concatenation_several_args = FormatTest(
        Concatenation(["A", "B", "C", 123]),
        "'A'||'B'||'C'||123",
    )
    test_concatws_several_args = FormatTest(
        ConcatWS(":", "ABC"),
        "'A'||':'||'B'||':'||'C'",
    )
    test_serialize_inttype_value = FormatTest(
        Expr("E", type=IntType()),
        "E",
        serialized=True,
    )
    test_serialize_booltype_value = FormatTest(
        Expr("E", type=BoolType()),
        "E",
        serialized=True,
    )
    test_query_where_no_table = FormatTest(
        Query().columns("A").where("B"),
        "SELECT A WHERE B",
    )

    def test_adjust_column_of_unknown_type_is_modified(self):
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "CAST(a AS BLOB)")


class AstDB(AstDB):
    pass


class TestSelectMethod(test_db_generic_methods.TestSelectMethod):
    db_class = AstDB
    compiler_class = sqlite.Compiler
    method_class = sqlite.SelectMethod


class TestSelectMethodPayloads(unittest.IsolatedAsyncioTestCase):
    """SQLite has a few changes to the normal (de)serialisation behaviour."""

    async def asyncSetUp(self):
        self.compiler = sqlite.Compiler(quote=quoting.singlequote)

        class MockSelectMethod(sqlite.SelectMethod):
            tag_hex_null = "HEXNULL"
            tag_null = "TAGNULL"

        self.method_with_hex = MockSelectMethod(
            self.compiler, None, hex=True, columns=1, column=0, nb_rows=1
        )
        self.method_without_hex = MockSelectMethod(
            self.compiler, None, hex=False, columns=1, column=0, nb_rows=1
        )

    def assertFormat(self, value: Node, string, formatter=""):
        assert isinstance(value, Node), f"Not a node: {value!r}"
        self.compiler.wrap(value)
        value = format(value, formatter)
        try:
            self.assertEqual(value, string)
        except AssertionError as e:
            raise e from None

    def test_serialize_cell_with_hex(self):
        self.assertFormat(
            self.method_with_hex.serialize_cell(Expr("column", type=TextType())),
            "HEX(COALESCE(column,'HEXNULL'))",
        )
        self.assertFormat(
            self.method_with_hex.serialize_cell(Expr("column", type=BlobType())),
            "HEX(COALESCE(column,'HEXNULL'))",
        )

    def test_serialize_cell_without_hex(self):
        self.assertFormat(
            self.method_without_hex.serialize_cell(Expr("column", type=TextType())),
            "COALESCE(column,'TAGNULL')",
        )
        self.assertFormat(
            self.method_without_hex.serialize_cell(Expr("column", type=BlobType())),
            "HEX(COALESCE(column,'HEXNULL'))",
        )

    def test_deserialize_cell_without_hex_for_text(self):
        self.assertEqual(
            self.method_without_hex.deserialize_cell(
                Expr("column", type=TextType()), b"test123"
            ),
            "test123",
        )
        self.assertEqual(
            self.method_without_hex.deserialize_cell(
                Expr("column", type=TextType()), b"test123"
            ),
            "test123",
        )

    def test_deserialize_cell_without_hex_for_blob(self):
        self.assertEqual(
            self.method_without_hex.deserialize_cell(
                Expr("column", type=BlobType()), b"414243"
            ),
            b"ABC",
        )

    def test_deserialize_cell_with_hex_for_text(self):
        self.assertEqual(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=TextType()), b"414243"
            ),
            "ABC",
        )
        self.assertEqual(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=TextType()), b"TAGNULL".hex().encode()
            ),
            "TAGNULL",
        )
        self.assertIsNone(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=TextType()), b"HEXNULL".hex().encode()
            )
        )

    def test_deserialize_cell_with_hex_for_blob(self):
        self.assertEqual(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=BlobType()), b"414243"
            ),
            b"ABC",
        )
        self.assertIsNone(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=BlobType()), b"HEXNULL".hex().encode()
            )
        )
        self.assertEqual(
            self.method_with_hex.deserialize_cell(
                Expr("column", type=BlobType()), b"TAGNULL".hex().encode()
            ),
            b"TAGNULL",
        )


class TestCountSeveralColumns(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.compiler = sqlite.Compiler(quote=quoting.singlequote)

    def test_count(self):
        method = sqlite.SelectMethod(
            self.compiler, None, hex=False, columns=2, column=0, nb_rows=1
        )
        query = Query("a").columns("b", "c")
        query = method._sql_get_count(query)
        self.compiler.wrap(query)
        self.assertEqual(str(query), "SELECT COUNT(*) FROM a")

    def test_count_distinct(self):
        method = sqlite.SelectMethod(
            self.compiler, None, hex=False, columns=2, column=0, nb_rows=1
        )
        query = Query("a").columns("b", "c").distinct()
        query = method._sql_get_count(query)
        self.compiler.wrap(query)
        self.assertRegex(
            str(query),
            r"SELECT COUNT\(\*\) FROM \(SELECT DISTINCT b,c FROM a\) AS [a-zA-Z]{4}",
        )


class TestTestMethodPayloads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.compiler = sqlite.Compiler(quote=quoting.singlequote)
        self.method = sqlite.TestMethod(self.compiler, None)

    def test_get_text_char(self):
        payload = Identifier("column")

        self.assertEqual(
            self.compiler.compile(self.method.fetcher_text.get_part(payload, 2, 1)),
            "SUBSTR(column,3,1)",
        )

    async def test_text_part_is_in(self):
        payload = Identifier("column", type=TextType(charset="ABC"))

        async def inject_return(payload):
            return payload

        candidates = await self.method.fetcher_text.get_candidates(payload)
        self.assertEqual(candidates, (65, 66, 67))

        self.method.fetcher_text.inject = inject_return
        self.assertEqual(
            self.compiler.compile(
                await self.method.fetcher_text.fetch_part_is_in(payload, candidates)
            ),
            "unicode(column) IN (65,66,67)",
        )

    def test_get_blob_byte(self):
        payload = Identifier("column")
        self.assertEqual(
            self.compiler.compile(self.method.fetcher_blob.get_part(payload, 2, 1)),
            "SUBSTR(column,3,1)",
        )

    async def test_fetch_blob_byte_is_in(self):
        payload = Identifier("column", type=BlobType(byteset=b"ABC"))

        async def inject_return(payload):
            return payload

        candidates = await self.method.fetcher_blob.get_candidates(payload)
        self.assertEqual(candidates, ("41", "42", "43"))

        self.method.inject = inject_return
        self.assertEqual(
            self.compiler.compile(
                await self.method.fetcher_blob.fetch_part_is_in(payload, candidates)
            ),
            "HEX(column) IN ('41','42','43')",
        )


class MockSelectMethod(sqlite.SelectMethod):
    tag_hex_null = "HEXNULL"
    tag_null = "n"


class TestHexDisplayMixinWithHex(test_db_generic_methods.TestHexDisplayMixinWithHex):
    compiler_class = sqlite.Compiler
    method_class = MockSelectMethod
    method_args = {
        "hex": True,
        "columns": 1,
        "column": 0,
        "nb_rows": 1,
    }

    test_serialize_cell_for_text = SerializeCellTest(
        Identifier("a", type=TextType()),
        "HEX(COALESCE(a,0x4845584e554c4c))",
    )
    test_serialize_cell_for_text_not_nullable = SerializeCellTest(
        Identifier("a", type=TextType(), nullable=False),
        "HEX(a)",
    )
    test_serialize_cell_for_blob = SerializeCellTest(
        Identifier("a", type=BlobType()),
        "HEX(COALESCE(a,0x4845584e554c4c))",
    )
    test_serialize_cell_for_blob_not_nullable = SerializeCellTest(
        Identifier("a", type=BlobType(), nullable=False),
        "HEX(a)",
    )
    test_serialize_cell_for_int = SerializeCellTest(
        Identifier("a", type=IntType()),
        "COALESCE(a,0x6e)",
    )
    test_serialize_cell_for_int_not_nullable = SerializeCellTest(
        Identifier("a", type=IntType(), nullable=False),
        "a",
    )
    test_serialize_cell_for_bool = SerializeCellTest(
        Identifier("a", type=BoolType()),
        "COALESCE(a,0x6e)",
    )
    test_serialize_cell_for_bool_not_nullable = SerializeCellTest(
        Identifier("a", type=BoolType(), nullable=False),
        "a",
    )


test_db_generic_methods.build_all_test_methods_variations(globals(), AstDB, sqlite)


class MapDummyMethod(generic.Method):
    def __init__(self, compiler: Compiler, inject: InjectForBytes, checker):
        super().__init__(compiler, inject)
        self.checker = checker

    async def fetch_results(self, query: Query, ctx: Context) -> Table:
        self.compiler.wrap(query)
        return self.checker(query)


class TestMapper(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.compiler = sqlite.Compiler(quote=quoting.singlequote)

    def _get_mapper(self, checker):
        method = MapDummyMethod(self.compiler, None, checker)
        return sqlite.Mapper(method)

    def _test_query(self, query: Query):
        query = str(query)

        # databases: no query expected
        # (handled by test_databases raising directly — not routed here)

        # tables: basic listing
        if query == "SELECT name FROM sqlite_schema WHERE type='table'":
            return [["table1"], ["table2"]]

        # tables: filter by exact table name
        if query == "SELECT name FROM sqlite_schema WHERE type='table' AND name='test'":
            return [["test"]]

        # tables: existence check for exact table
        if (
            query
            == "SELECT (SELECT COUNT(name) FROM sqlite_schema WHERE type='table' AND name='test')!=0"
        ):
            return [[True]]

        # columns: for table1 (name only)
        if query == "SELECT name FROM pragma_table_info('table1')":
            return [["column1"], ["column2"]]

        # columns: for table2 (name only)
        if query == "SELECT name FROM pragma_table_info('table2')":
            return [["column3"]]

        # columns: for 'test' table (name only)
        if query == "SELECT name FROM pragma_table_info('test')":
            return [["column1"], ["column2"]]

        # types: for table1 (name + type)
        if query == "SELECT name,type FROM pragma_table_info('table1')":
            return [["column1", "text"], ["column2", "int"]]

        # types: for table2 (name + type)
        if query == "SELECT name,type FROM pragma_table_info('table2')":
            return [["column3", "varchar"]]

        if (
            query
            == "SELECT COUNT(*)!=0 FROM pragma_table_info('test') WHERE name='column1'"
        ):
            return [["1"]]

        # columns filtered by wildcard column name: tables having a matching column
        if re.match(
            r"SELECT name FROM sqlite_schema AS .*? WHERE type='table' AND \(SELECT COUNT\(\*\)!=0 FROM pragma_table_info\(.*?.name\) WHERE name LIKE 'column%'\)",
            query,
        ):
            return [["test"]]

        # columns filtered by wildcard column name (col*mn* variant): tables having a matching column
        if re.match(
            r"SELECT name FROM sqlite_schema AS .*? WHERE type='table' AND \(SELECT COUNT\(\*\)!=0 FROM pragma_table_info\(.*?.name\) WHERE name LIKE 'col%mn%'\)",
            query,
        ):
            return [["test"]]

        # columns filtered by wildcard column name (col*mn* variant): tables having a matching column
        # SELECT name FROM sqlite_schema AS uWpJ WHERE type='table' AND (SELECT COUNT(*)!=0 FROM pragma_table_info(uWpJ.name) WHERE name LIKE 'col%mn%')
        if re.match(
            r"SELECT name FROM sqlite_schema AS .*? WHERE type='table' AND \(SELECT COUNT\(\*\)!=0 FROM pragma_table_info\(.*?.name\) WHERE name LIKE 'col%mn%'\)",
            query,
        ):
            return [["test"]]

        # columns: filter by wildcard on 'test' table
        if (
            query
            == "SELECT name FROM pragma_table_info('test') WHERE name LIKE 'column%'"
        ):
            return [["column1"], ["column2"]]

        # columns: existence check for exact column name in 'test' table
        if (
            query
            == "SELECT COUNT(name)!=0 FROM pragma_table_info('test') WHERE name='column1'"
        ):
            return [["column1"], ["column2"]]

        # columns: tables having exact column name (no wildcard)
        if re.match(
            r"SELECT name FROM sqlite_schema AS .{4} WHERE type='table' AND \(SELECT COUNT\(\*\)!=0 FROM pragma_table_info\(.{4}\.name\) WHERE name='column1'\)",
            query,
        ):
            return [["test"]]

        self.fail(f"Unexpected query: {query}")

    async def test_databases(self):
        def test(query: Query):
            raise ValueError("No query should get started, it returns a dummy result")

        mapper = self._get_mapper(test)
        result = await mapper.fetch("databases")
        self.assertEqual(result.items, {"root": {}})

    async def test_tables(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("tables")
        self.assertEqual(result.items, {"root": {"table1": {}, "table2": {}}})

    async def test_columns(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("columns")
        self.assertEqual(
            result.items,
            {
                "root": {
                    "table1": {"column1": {}, "column2": {}},
                    "table2": {"column3": {}},
                }
            },
        )

    async def test_types(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("types")
        self.assertEqual(
            result.items,
            {
                "root": {
                    "table1": {"column1": {"type": "text"}, "column2": {"type": "int"}},
                    "table2": {"column3": {"type": "varchar"}},
                }
            },
        )

    async def test_filtering_by_database_is_not_implemented(self):
        mapper = self._get_mapper(self._test_query)
        with self.assertRaises(NotImplementedError):
            await mapper.fetch("types", database="test")

    async def test_filtering_tables_by_column(self):
        mapper = self._get_mapper(self._test_query)
        await mapper.fetch("tables", column="col*mn*")

    async def test_fetch_tables_filtering_by_exact_table(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("tables", table="test")
        self.assertEqual(result.items, {"root": {"test": {}}})

    async def test_fetch_columns_filtering_by_exact_table(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("columns", table="test")
        self.assertEqual(
            result.items, {"root": {"test": {"column1": {}, "column2": {}}}}
        )

    async def test_fetch_columns_filtering_by_exact_column(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("columns", column="column1")
        self.assertEqual(result.items, {"root": {"test": {"column1": {}}}})

    async def test_fetch_columns_filtering_by_wildcarded_column(self):
        mapper = self._get_mapper(self._test_query)
        result = await mapper.fetch("columns", column="column*")
        self.assertEqual(
            result.items, {"root": {"test": {"column1": {}, "column2": {}}}}
        )
