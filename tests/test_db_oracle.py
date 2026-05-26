import unittest
from unittest import mock

from ending.ast import *
from ending.db import oracle
from ending.exception import InjectionError
from ending.struct.metadata import VoidContext
from ending.util import quoting
from ending.util.misc import to_bytes
from tests import test_db_generic_methods
from tests.test_db_generic_compiler import FormatTest, base
from tests import test_db_generic_compiler


class TestCompiler(test_db_generic_compiler.TestCompiler):
    compiler_class = oracle.Compiler

    test_substring = FormatTest(
        Substring("x", 11, 3),
        "SUBSTR(x,12,3)",
    )
    test_serialize_inttype_value = FormatTest(
        Expr("E", type=IntType()),
        "CAST(E AS VARCHAR(100))",
        serialized=True,
    )
    test_serialize_booltype_value = FormatTest(
        Expr("E", type=BoolType()),
        "CASE E WHEN TRUE THEN '1' WHEN FALSE THEN '0' END",
        serialized=True,
    )
    test_serialize_blobtype_value = FormatTest(
        Expr("E", type=BlobType()),
        "RAWTOHEX(DBMS_LOB.SUBSTR(E))",
        serialized=True,
    )
    test_concatws_several_args = FormatTest(
        ConcatWS(":", "ABC"),
        "'A'||':'||'B'||':'||'C'",
    )
    test_concatenation_several_args = FormatTest(
        Concatenation(["A", "B", "C", 123]),
        "'A'||'B'||'C'||123",
    )
    test_hex_texttype = FormatTest(
        Hex(Identifier("A", type=TextType())),
        "RAWTOHEX(A)",
    )
    test_hex_blobtype = FormatTest(
        Hex(Identifier("A", type=BlobType())),
        "RAWTOHEX(DBMS_LOB.SUBSTR(A))",
    )
    test_length_texttype = FormatTest(
        Length(Identifier("OP1", type=TextType())),
        "LENGTH(OP1)",
    )
    test_length_blobtype = FormatTest(
        Length(Identifier("OP1", type=BlobType())),
        "DBMS_LOB.GETLENGTH(OP1)",
    )
    test_ord_blobtype = FormatTest(
        Ord(Identifier("x", type=BlobType())),
        "UTL_RAW.CAST_TO_BINARY_INTEGER(x)",
    )
    test_ord_texttype = FormatTest(
        Ord(Identifier("x", type=TextType())),
        "ASCII(x)",
    )
    test_value_booltype_true = FormatTest(
        Value(True),
        "TRUE",
    )
    test_value_booltype_false = FormatTest(
        Value(False),
        "FALSE",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "SUBSTR(x,2)",
    )
    test_query_reset_no_reset = FormatTest(
        Query("T").columns("C").where("1=1").order("C").limit(1, 1),
        regex=r"SELECT C FROM \(SELECT C,ROWNUM ([a-zA-Z]{4}) FROM T WHERE 1=1 ORDER BY C\) [a-zA-Z]{4} WHERE \1=2",
    )
    test_query_limit_0_10 = FormatTest(
        base().limit(10),
        regex=r"^SELECT column FROM \(SELECT column,ROWNUM ([a-zA-Z]{4}) FROM table\) ([a-zA-Z]{4}) WHERE \1<11$",
    )
    test_query_limit_5_10 = FormatTest(
        base().limit(5, 10),
        regex=r"^SELECT column FROM \(SELECT column,ROWNUM ([a-zA-Z]{4}) FROM table\) ([a-zA-Z]{4}) WHERE \1>5 AND \1<16$",
    )
    test_query_column = FormatTest(
        Query().columns("col"),
        "SELECT col FROM DUAL",
    )
    test_query_limit_no_table = FormatTest(
        Query().columns("A").limit(1),
        regex=r"SELECT A FROM \(SELECT A,ROWNUM ([a-zA-Z]{4}) FROM DUAL\) ([a-zA-Z]{4}) WHERE \1=1$",
    )
    test_alias = FormatTest(
        Alias("x", "y"),
        "x y",
    )

    def test_adjust_column_of_unknown_type_is_modified(self):
        # The default compiler produces no changes
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "CAST(a AS VARCHAR(4000))")

    def test_alias_of_query_gives_complete_query(self):
        self.assertFormat(Alias(Query().columns("x"), "y"), "(SELECT x) y")

    def test_alias_of_query_gives_complete_query(self):
        self.assertFormat(Alias(Query().columns("x"), "y"), "(SELECT x FROM DUAL) y")


class AstDB(test_db_generic_methods.AstDB):
    def resolve_Function(self, function: Function):
        if function.name == "RAWTOHEX":
            data = to_bytes(self.resolve(function.args[0]))
            if data is not None:
                return data.hex()
            return None
        return super().resolve_Function(function)


class TestSelectMethod(test_db_generic_methods.TestSelectMethod):
    db_class = AstDB
    compiler_class = oracle.Compiler
    method_class = oracle.SelectMethod


class TestTestMethodPayloads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.compiler = oracle.Compiler(quote=quoting.singlequote)
        self.method = oracle.TestMethod(self.compiler, None)

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
            "ASCII(column) IN (65,66,67)",
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
        self.assertEqual(candidates, (65, 66, 67))

        self.method.fetcher_blob.inject = inject_return
        self.assertEqual(
            self.compiler.compile(
                await self.method.fetcher_blob.fetch_part_is_in(payload, candidates)
            ),
            "UTL_RAW.CAST_TO_BINARY_INTEGER(column) IN (65,66,67)",
        )


test_db_generic_methods.build_all_test_methods_variations(globals(), AstDB, oracle)


class TestAdjustForBlobMixin(unittest.IsolatedAsyncioTestCase):
    class FakeMethod(oracle.api.AdjustForBlobMixin):
        async def fetch_results(self, query, ctx):
            return [[]]

    async def test_adjust_blob_with_errors(self):
        compiler = oracle.Compiler(quote=quoting.singlequote)

        query = Query().columns(
            Identifier("a"), Identifier("b", type=TextType()), Identifier("c")
        )

        with mock.patch.object(
            self.__class__.FakeMethod, "_column_is_errored", return_value=True
        ):
            method = self.__class__.FakeMethod(compiler, None)
            new_query = await method.adjust_query(query, ctx=VoidContext())

        self.assertIsInstance(new_query.q.columns[0].metadata.type, BlobType)
        self.assertIsInstance(new_query.q.columns[1].metadata.type, TextType)
        self.assertIsInstance(new_query.q.columns[2].metadata.type, BlobType)

    async def test_adjust_blob_with_no_errors(self):
        compiler = oracle.Compiler(quote=quoting.singlequote)

        query = Query().columns(
            Identifier("a"), Identifier("b", type=TextType()), Identifier("c")
        )

        with mock.patch.object(
            self.__class__.FakeMethod, "_column_is_errored", return_value=False
        ):
            method = self.__class__.FakeMethod(compiler, None)
            new_query = await method.adjust_query(query, ctx=VoidContext())

        self.assertIsInstance(new_query.q.columns[0].metadata.type, TextType)
        self.assertIsInstance(new_query.q.columns[1].metadata.type, TextType)
        self.assertIsInstance(new_query.q.columns[2].metadata.type, TextType)

    async def test_column_is_errored_returns_true_in_case_of_an_exception(self):
        compiler = oracle.Compiler(quote=quoting.singlequote)

        query = Query().columns(
            Identifier("a"), Identifier("b", type=TextType()), Identifier("c")
        )

        with mock.patch.object(
            self.__class__.FakeMethod,
            "fetch_results",
            side_effect=InjectionError("error"),
        ):
            method = self.__class__.FakeMethod(compiler, None)
            result = await method._column_is_errored(query, Identifier("test"))

        self.assertTrue(result)

    async def test_column_is_errored_returns_true_in_case_of_identical_results(self):
        compiler = oracle.Compiler(quote=quoting.singlequote)

        query = Query().columns(
            Identifier("a"), Identifier("b", type=TextType()), Identifier("c")
        )

        with mock.patch.object(
            self.__class__.FakeMethod, "fetch_results", return_value=[[True]]
        ):
            method = self.__class__.FakeMethod(compiler, None)
            result = await method._column_is_errored(query, Identifier("test"))

        self.assertTrue(result)

    async def test_column_is_errored_works_fine(self):
        compiler = oracle.Compiler(quote=quoting.singlequote)

        query = Query().columns(
            Identifier("a"), Identifier("b", type=TextType()), Identifier("c")
        )

        result = False

        async def fetch_results(self, query: Query, *args, **kwargs):
            return [[query.q.columns[0].negate]]

        with mock.patch.object(
            self.__class__.FakeMethod, "fetch_results", fetch_results
        ):
            method = self.__class__.FakeMethod(compiler, None)
            result = await method._column_is_errored(query, Identifier("test"))

        self.assertFalse(result)
