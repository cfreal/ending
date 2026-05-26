import re
import unittest

from ending import configuration
from ending.ast import *
from ending.ast import BoolType, IntType
from ending.db import mssql
from ending.exception import CompilationError
from ending.util import quoting
from ending.util.misc import to_bytes
from tests import test_db_generic_methods
from tests.test_db_generic_compiler import FormatTest, TestConcatWSMixin, base
from tests import test_db_generic_compiler

from .db_testing import BaseDBTestCaseMixin


class AstDB(test_db_generic_methods.AstDB):
    def resolve_Function(self, function: Function):
        if function.name.upper() == "CONVERT":
            value = self.resolve(function.args[1])

            if isinstance(value, bool):
                value = b"1" if value else b"0"
            else:
                value = to_bytes(value)
            # HEX
            if len(function.args) == 3 and function.args[2].value == 2:
                return value.hex().encode()
            return value

        return super().resolve_Function(function)


class TestCompiler(TestConcatWSMixin, test_db_generic_compiler.TestCompiler):
    compiler_class = mssql.Compiler

    test_query_limit_0_10 = FormatTest(
        base().limit(10),
        "SELECT column FROM table ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
    )

    test_query_limit_0_10 = FormatTest(
        base().limit(10),
        "SELECT column FROM table ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
    )

    test_query_limit_5_10 = FormatTest(
        base().limit(5, 10),
        "SELECT column FROM table ORDER BY 1 OFFSET 5 ROWS FETCH NEXT 10 ROWS ONLY",
    )

    test_query_limit_no_table = FormatTest(
        Query().columns("A").limit(1),
        "SELECT A ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY",
    )

    test_query_with_only_single_columns_orders_by_1 = FormatTest(
        Query().columns(Identifier("A", single=True)).limit(1),
        "SELECT A ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY",
    )

    test_limit = FormatTest(
        Limit(5, 10),
        "OFFSET 5 ROWS FETCH NEXT 10 ROWS ONLY",
    )

    test_limit_with_start_at_0 = FormatTest(
        Limit(0, 10),
        "OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
    )

    def test_value_booltype_true(self):
        with self.assertRaises(CompilationError) as cm:
            self.compiler.compile(Value(True))
        self.assertEqual(str(cm.exception), "Boolean values are not supported in MsSQL")

    def test_value_booltype_false(self):
        with self.assertRaises(CompilationError) as cm:
            self.compiler.compile(Value(False))
        self.assertEqual(str(cm.exception), "Boolean values are not supported in MsSQL")

    test_query_reset_no_reset = FormatTest(
        Query("T").columns("C").where("1=1").order("C").limit(1, 1),
        "SELECT C FROM T WHERE 1=1 ORDER BY C OFFSET 1 ROWS FETCH NEXT 1 ROWS ONLY",
    )
    test_concatenation_several_args = FormatTest(
        Concatenation(["A", "B", "C", 123]), "'A'+'B'+'C'+123"
    )

    test_serialize_texttype_value = FormatTest(
        Expr("E", type=TextType()),
        "E",
        serialized=True,
    )
    test_serialize_inttype_value = FormatTest(
        Expr("E", type=IntType()),
        "CAST(E AS VARCHAR)",
        serialized=True,
    )
    test_serialize_booltype_value = FormatTest(
        Expr("E", type=BoolType()),
        "IIF(E,'1','0')",
        serialized=True,
    )
    test_serialize_blobtype_value = FormatTest(
        Expr("E", type=BlobType()),
        "CONVERT(VARCHAR(max),E,2)",
        serialized=True,
    )
    test_hex_texttype = FormatTest(
        Hex(Identifier("A", type=TextType())),
        "CONVERT(VARCHAR(max),convert(VARBINARY(max),A),2)",
    )
    test_hex_blobtype = FormatTest(
        Hex(Identifier("A", type=BlobType())),
        "CONVERT(VARCHAR(max),A,2)",
    )
    test_query_where_no_table = FormatTest(
        Query().columns("A").where("B"),
        "SELECT A WHERE B",
    )
    test_ord_texttype = FormatTest(
        Ord(Identifier("x", type=TextType())),
        "UNICODE(x)",
    )
    test_ord_blobtype = FormatTest(
        Ord(Identifier("x", type=BlobType())),
        "ASCII(x)",
    )
    test_length_texttype = FormatTest(
        Length(Identifier("OP1", type=TextType())),
        "LEN(OP1)",
    )
    test_length_blobtype = FormatTest(
        Length(Identifier("OP1", type=BlobType())),
        "LEN(OP1)",
    )

    def test_adjust_column_of_unknown_type_is_modified(self):
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "convert(VARBINARY(max),CAST(a AS VARCHAR(max)))")

    def test_adjust_column_of_bool_gets_converted_to_bit(self):
        column = Identifier("a", type=BoolType())
        column = self.compiler._adjust_column(column)
        self.assertIsInstance(column.metadata.type, IntType)
        self.assertEqual(column.metadata.type.min, 0)
        self.assertEqual(column.metadata.type.max, 1)

    def test_adjust_column_of_standard_types_is_identity(self):
        types = [
            # BoolType(), # This one will get changed
            IntType(),
            TextType(),
            BlobType(),
        ]
        for type in types:
            column = Identifier("A", type=type)
            self.assertIs(self.compiler._adjust_column(column), column)


class TestSelectMethod(test_db_generic_methods.TestSelectMethod):
    db_class = AstDB
    compiler_class = mssql.Compiler
    method_class = mssql.SelectMethod


class TestCastAsIntMethod(unittest.IsolatedAsyncioTestCase):
    def test_payload(self):
        class MockCastAsIntMethod(mssql.CastAsIntMethod):
            tag_stop = "TAGSTOP"

        compiler = mssql.Compiler(quote=quoting.singlequote)
        method = MockCastAsIntMethod(compiler, None)
        query = Query("table").columns("A").limit(3, 1)
        payload = method.build_payload(query, 123)
        compiler.wrap(payload)

        self.assertEqual(
            str(payload),
            "CAST(':'+SUBSTRING((SELECT A+'T'+'AGSTOP' FROM table ORDER BY 1 OFFSET 3 ROWS FETCH NEXT 1 ROWS ONLY),124,1048576) AS int)=1",
        )


# This is just to have a Design class for the configurator test
class Design:
    pass


class TestTestMethodConfigurator(unittest.IsolatedAsyncioTestCase):
    async def test_configurator_does_not_use_bools_and_succeeds(self) -> None:
        ConfiguratorClass = mssql.TestMethod.get_configurator()

        async def _fetch_value(query: Query, **params) -> bool:
            match query.q.columns[0]:
                case Comparison(operator="="):
                    return True
                case Comparison(operator="!="):
                    return False
                case _:
                    self.fail(f"Unexpected query: {query!r}")

        ConfiguratorClass._fetch_value = _fetch_value

        await ConfiguratorClass._verify(ConfiguratorClass)

    async def test_configurator_does_not_use_bools_and_fails(self) -> None:
        ConfiguratorClass = mssql.TestMethod.get_configurator()
        configurator = ConfiguratorClass(
            Design(), mssql, quoting.singlequote, None, None, mssql.TestMethod
        )

        async def _fetch_value(query: Query, **params) -> bool:
            match query.q.columns[0]:
                case Comparison(operator="="):
                    return True
                case Comparison(operator="!="):
                    return True
                case _:
                    self.fail(f"Unexpected query: {query!r}")

        configurator._fetch_value = _fetch_value

        with self.assertRaises(configuration.ConfigurationException) as cm:
            await configurator._verify()
        self.assertIn("Unable to configure **TestMethod**", str(cm.exception))


test_db_generic_methods.build_all_test_methods_variations(globals(), AstDB, mssql)
