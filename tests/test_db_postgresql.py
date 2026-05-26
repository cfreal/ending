import unittest

from ending.ast import *
from ending.ast import BoolType, Function, IntType
from ending.db import postgresql
from ending.db.generic.compiler import Compiler
from ending.util import quoting
from tests import test_db_generic_methods
from tests.test_db_generic_compiler import FormatTest, TestConcatWSMixin, base
from tests import test_db_generic_compiler

from .db_testing import AstDB, BaseDBTestCaseMixin


class AstDB(AstDB):
    def resolve_ColonCast(self, node: postgresql.ColonCast) -> Node:
        value = self.resolve(node.node)
        cast = node.cast
        if cast == "bytea":
            return value.encode() if isinstance(value, str) else value
        if cast == "text":
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)
        if cast == "int":
            return int(value)
        return value

    def resolve_Function(self, function: Function):
        if function.name == "get_byte":
            value = self.resolve(function.args[0])
            idx = self.resolve(function.args[1])
            return value[idx]
        return super().resolve_Function(function)


class PostgresDBMixin(BaseDBTestCaseMixin):
    def get_db(self, results) -> AstDB:
        return AstDB(results)

    def get_compiler(self) -> Compiler:
        return postgresql.Compiler(quote=quoting.singlequote)


class TestCompiler(TestConcatWSMixin, test_db_generic_compiler.TestCompiler):
    compiler_class = postgresql.Compiler

    test_substring = FormatTest(
        Substring("x", 11, 3),
        "substring(x,12,3)",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "substring(x,2)",
    )
    test_query_limit_5_10 = FormatTest(
        base().limit(5, 10), "SELECT column FROM table LIMIT 10 OFFSET 5"
    )
    test_query_where_no_table = FormatTest(
        Query().columns("A").where("B"),
        "SELECT A WHERE B",
    )
    test_query_reset_no_reset = FormatTest(
        Query("T").columns("C").where("1=1").order("C").limit(1, 1),
        "SELECT C FROM T WHERE 1=1 ORDER BY C LIMIT 1 OFFSET 1",
    )
    test_value_booltype_true = FormatTest(
        Value(True),
        "TRUE",
    )
    test_value_booltype_false = FormatTest(
        Value(False),
        "FALSE",
    )
    test_limit = FormatTest(
        Limit(5, 10),
        "LIMIT 10 OFFSET 5",
    )
    test_coloncast = FormatTest(
        postgresql.ColonCast(Identifier("x"), "bytea"),
        "x::bytea",
    )
    test_coloncast_value = FormatTest(
        postgresql.ColonCast(Value("x"), "bytea"),
        "'x'::bytea",
    )
    test_coloncast_function = FormatTest(
        postgresql.ColonCast(Function["test"]("x", "y"), "bytea"),
        "test('x','y')::bytea",
    )
    test_coloncast_with_complex_node_adds_parenthesis = FormatTest(
        postgresql.ColonCast(Identifier("x") == Identifier("y"), "text"),
        "(x=y)::text",
    )
    test_hex_texttype = FormatTest(
        Hex(Identifier("A", type=TextType())),
        "encode(REPLACE(A,'\\','\\\\')::bytea,'hex')",
    )
    test_hex_blobtype = FormatTest(
        Hex(Identifier("A", type=BlobType())),
        "encode(A,'hex')",
    )
    test_serialize_inttype_value = FormatTest(
        Expr("E", type=IntType()),
        "(E)::text",
        serialized=True,
    )
    test_serialize_booltype_value = FormatTest(
        Expr("E", type=BoolType()),
        "(E)::int::text",
        serialized=True,
    )
    test_serialize_blobtype_value = FormatTest(
        Expr("E", type=BlobType()),
        "encode(E,'hex')",
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
    test_ord_blobtype = FormatTest(
        Ord(Identifier("x", type=BlobType())),
        "get_byte(x,0)",
    )
    test_ord_texttype = FormatTest(
        Ord(Identifier("x", type=TextType())),
        "ascii(x)",
    )

    def test_adjust_column_of_unknown_type_is_modified(self):
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "a::text")


class TestSelectMethod(test_db_generic_methods.TestSelectMethod):
    db_class = AstDB
    compiler_class = postgresql.Compiler
    method_class = postgresql.SelectMethod


test_db_generic_methods.build_all_test_methods_variations(globals(), AstDB, postgresql)


class TestCastAsIntMethod(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.compiler = postgresql.Compiler(quote=quoting.singlequote)
        self.method = postgresql.CastAsIntMethod(compiler=self.compiler, inject=None)

    def test_build_payload(self):
        payload = Query().columns(Identifier("COL"))
        self.assertRegex(
            self.compiler.compile(self.method.build_payload(payload, 2)),
            (
                r"CAST\(CONCAT\(':',substring\(CONCAT\(COL,'[^)]+'\),3,1048576\)\) AS int\)"
            ),
        )
