import unittest

from ending.ast import *
from ending.db import mysql
from ending.util import quoting
from ending.util.misc import to_bytes
from tests import test_db_generic_methods
from tests.test_db_generic_compiler import FormatTest, TestConcatWSMixin
from tests import test_db_generic_compiler


class TestCompiler(TestConcatWSMixin, test_db_generic_compiler.TestCompiler):
    compiler_class = mysql.Compiler

    test_substring = FormatTest(
        Substring("x", 11, 3),
        "SUBSTR(x,12,3)",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "SUBSTR(x,2)",
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
    test_serialize_blobtype_value = FormatTest(
        Expr("E", type=BlobType()),
        "HEX(E)",
        serialized=True,
    )
    test_length_texttype = FormatTest(
        Length(Identifier("OP1", type=TextType())),
        "CHAR_LENGTH(OP1)",
    )
    test_length_blobtype = FormatTest(
        Length(Identifier("OP1", type=BlobType())),
        "LENGTH(OP1)",
    )

    def test_adjust_column_of_unknown_type_is_modified(self):
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "CONVERT(a USING latin1)")

    def test_adjust_query_adjusts_columns(self):
        query = Query().columns(Identifier("a", type=UnknownType()))
        query = self.compiler.adjust_query(query)
        self.assertFormat(query, "SELECT CONVERT(a USING latin1)")


class AstDB(test_db_generic_methods.AstDB):
    def resolve_Function(self, function: Function):
        if function.name == "CHAR_LENGTH":
            return len(self.resolve(function.args[0]))
        if function.name == "ORD":
            return ord(self.resolve(function.args[0]))
        if function.name == "CONVERT":
            value = self.resolve(function.args[0])
            if isinstance(value, bool):
                return b"1" if value else b"0"
            else:
                return to_bytes(value)

        return super().resolve_Function(function)


class TestSelectMethod(test_db_generic_methods.TestSelectMethod):
    db_class = AstDB
    compiler_class = mysql.Compiler
    method_class = mysql.SelectMethod


class TestTestMethodPayloads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.compiler = mysql.Compiler(quote=quoting.singlequote)
        self.method = mysql.TestMethod(self.compiler, None)

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
            "ORD(column) IN (65,66,67)",
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
            "ORD(column) IN (65,66,67)",
        )


test_db_generic_methods.build_all_test_methods_variations(globals(), AstDB, mysql)


class TestExtractValueMethod(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.compiler = mysql.Compiler(quote=quoting.singlequote)
        self.method = mysql.ExtractValueMethod(compiler=self.compiler, inject=None)

    def test_build_payload(self):
        payload = Query().columns(Identifier("COL"))
        self.assertRegex(
            self.compiler.compile(self.method.build_payload(payload, 2)),
            (
                r"ExtractValue\('[^']+',CONCAT\('x\(',"
                r"SUBSTR\(CONCAT\(BINARY\(COL\),'[^)]+'\),3,31\)\)\)"
            ),
        )
