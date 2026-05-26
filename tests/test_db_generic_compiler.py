from __future__ import annotations
from copy import copy
import dataclasses
from functools import partial
import string
import unittest

from ending.ast import *
from ending.ast import Expression, LogicalOperation, Node, Query
from ending.db import generic, mysql
from ending.db.generic.compiler import HasConcatWSMixin
from ending.exception import ConversionError, NodeTypeError
from ending.util import quoting
from tests.db_testing import FormatTest, CompilerTestCase

__all__ = [
    "FormatTest",
    "TestConcatWSMixin",
    "TestCompiler",
    "MockCompiler",
]


class MockCompiler(generic.Compiler):
    compile_Query = mysql.Compiler.compile_Query

    def serialize(self, node: Node) -> Node:
        if isinstance(node.metadata.type, TextType):
            return node
        if isinstance(node.metadata.type, (BoolType, IntType)):
            return Cast(node, "TEXT", type=node.metadata.type.to_texttype())
        if isinstance(node.metadata.type, BlobType):
            return Hex(node)
        raise ConversionError(
            f"Cannot serialize type: {type(node.metadata.type).__name__}"
        )

    def _adjust_column(self, column: Node) -> Node:
        if isinstance(column.metadata.type, UnknownType):
            return column.with_type(TextType())
        return column


class TestFeatures(unittest.TestCase):
    def test_features_cannot_be_instanciated(self):
        with self.assertRaises(RuntimeError):
            generic.Features()


def combination() -> LogicalOperation:
    op1 = Identifier("OP1")
    op2 = Identifier("OP2")
    return (op1 <= op2) & (op1 >= op2)


def base() -> Query:
    return Query("table").columns("column")


class TestCompiler(CompilerTestCase, unittest.TestCase):
    compiler_class = MockCompiler

    # Alias

    test_alias = FormatTest(
        Alias("x", "y"),
        "x AS y",
    )
    test_alias_of_query_gives_complete_query = FormatTest(
        Alias(Query().columns("x"), "y"),
        "(SELECT x) AS y",
    )

    # Comparison

    test_arithmeticoperation_plus = FormatTest(
        ArithmeticOperation(Identifier("OP1"), "+", Identifier("OP2")),
        "OP1+OP2",
    )
    test_comparison_eq = FormatTest(
        (Identifier("OP1") == Identifier("OP2")),
        "OP1=OP2",
    )
    test_comparison_neq = FormatTest(
        Identifier("OP1") != Identifier("OP2"),
        "OP1!=OP2",
    )
    test_comparison_less_or_equal = FormatTest(
        Identifier("OP1") <= Identifier("OP2"),
        "OP1<=OP2",
    )
    test_comparison_and_combination = FormatTest(
        combination(),
        "OP1<=OP2 AND OP1>=OP2",
    )
    test_comparison_and_combination_or = FormatTest(
        (combination() | Identifier("OP2")),
        "(OP1<=OP2 AND OP1>=OP2) OR OP2",
    )

    # Concatenation

    test_concatenation_several_args = FormatTest(
        Concatenation(["A", "B", "C", 123]),
        "CONCAT('A','B','C',123)",
    )
    test_concatenation_no_args = FormatTest(
        Concatenation([]),
        "''",
    )
    test_concatenation_single_arg = FormatTest(
        Concatenation(["A"]),
        "'A'",
    )

    # Case

    test_case = FormatTest(
        Case(Expr("A"), (("B", "C"), ("D", "E")), "F"),
        "CASE A WHEN 'B' THEN 'C' WHEN 'D' THEN 'E' ELSE 'F' END",
    )
    test_case_no_condition = FormatTest(
        Case(None, (((Identifier("B") == "C"), "D"),), "F"),
        "CASE WHEN B='C' THEN 'D' ELSE 'F' END",
    )
    test_case_no_else = FormatTest(
        Case(Expr("A"), (("B", "C"), ("D", "E"))),
        "CASE A WHEN 'B' THEN 'C' WHEN 'D' THEN 'E' END",
    )
    test_case_no_cases = FormatTest(
        Case(Expr("A"), (), "F"),
        "CASE A ELSE 'F' END",
    )

    # Query

    test_query_column = FormatTest(
        Query().columns("col"),
        "SELECT col",
    )
    test_query_column_table = FormatTest(
        base(),
        "SELECT column FROM table",
    )
    test_query_columns_table = FormatTest(
        base().columns("column2", "column3"),
        "SELECT column2,column3 FROM table",
    )
    test_query_where = FormatTest(
        base().where("c0={} AND c1={}"),
        "SELECT column FROM table WHERE c0={} AND c1={}",
    )
    test_query_where_expr = FormatTest(
        base().where("c0={} AND c1={}", 123, 456),
        "SELECT column FROM table WHERE c0=123 AND c1=456",
    )
    test_query_order_desc = FormatTest(
        base().order("test", True),
        "SELECT column FROM table ORDER BY test DESC",
    )
    test_query_order = FormatTest(
        base().order("test"),
        "SELECT column FROM table ORDER BY test",
    )
    test_query_limit_0_10 = FormatTest(
        base().limit(10),
        "SELECT column FROM table LIMIT 10",
    )
    test_query_limit_5_10 = FormatTest(
        base().limit(5, 10),
        "SELECT column FROM table LIMIT 5,10",
    )
    test_query_where_combination = FormatTest(
        base().where(combination()),
        "SELECT column FROM table WHERE OP1<=OP2 AND OP1>=OP2",
    )
    test_query_distinct = FormatTest(
        base().distinct(),
        "SELECT DISTINCT column FROM table",
    )
    test_query_limit_no_table = FormatTest(
        Query().columns("A").limit(1),
        "SELECT A LIMIT 1",
    )
    test_query_where_no_table = FormatTest(
        Query().columns("A").where("B"),
        "SELECT A FROM DUAL WHERE B",
    )
    test_query_reset_order = FormatTest(
        base().order("name").order(None),
        "SELECT column FROM table",
    )
    test_query_reset_where = FormatTest(
        base().where("1=1").where(None),
        "SELECT column FROM table",
    )
    test_query_reset_limit = FormatTest(
        base().limit(10).limit(None),
        "SELECT column FROM table",
    )
    test_query_parenthesized_simplify_no_table = FormatTest(
        Query().columns("column1"), "column1", formatter="p"
    )
    test_query_parenthesized_simplify_with_table = FormatTest(
        base(), "(SELECT column FROM table)", formatter="p"
    )
    test_query_reset_no_reset = FormatTest(
        Query("T").columns("C").where("1=1").order("C").limit(1, 1),
        "SELECT C FROM T WHERE 1=1 ORDER BY C LIMIT 1,1",
    )
    test_union = FormatTest(
        Union(base(), base(), all=False),
        "SELECT column FROM table UNION SELECT column FROM table",
    )
    test_union_all = FormatTest(
        Union(base(), base(), all=True),
        "SELECT column FROM table UNION ALL SELECT column FROM table",
    )

    # Count

    test_count_empty = FormatTest(
        Count(),
        "COUNT(*)",
    )
    test_count_single = FormatTest(
        Count(Identifier("test")),
        "COUNT(test)",
    )
    test_count_distinct = FormatTest(
        Count("test", distinct=True),
        "COUNT(DISTINCT test)",
    )

    # Value

    test_value_str = FormatTest(
        Value("abc\"def'ghi"),
        "'abc\"def''ghi'",
    )
    test_value_booltype_true = FormatTest(
        Value(True),
        "1",
    )
    test_value_booltype_false = FormatTest(
        Value(False),
        "0",
    )
    test_value_none = FormatTest(
        Value(None),
        "NULL",
    )
    test_value_inttype = FormatTest(
        Value(123),
        "123",
    )
    test_value_float = FormatTest(
        Value(123.1),
        "123.1",
    )

    def test_value_bytes(self):
        with self.assertRaisesRegex(TypeError, "^Cannot compile Value for type bytes$"):
            self.assertFormat(Value(b"test"), "will not be used")

    def test_value_str_hexadecimal(self):
        self.compiler = self.get_compiler(quote=quoting.hexadecimal)
        self.assertFormat(Value("abc\"def'ghi"), "0x6162632264656627676869")

    # Other

    test_not = FormatTest(
        Not(Identifier("keyword")),
        "NOT (keyword)",
    )
    test_is_null = FormatTest(
        IsNull(combination()),
        "(OP1<=OP2 AND OP1>=OP2) IS NULL",
    )
    test_is_not_null = FormatTest(
        IsNull(combination(), negate=True),
        "(OP1<=OP2 AND OP1>=OP2) IS NOT NULL",
    )
    test_like = FormatTest(
        combination().like("SOMETHING%"),
        "(OP1<=OP2 AND OP1>=OP2) LIKE 'SOMETHING%'",
    )
    test_list = FormatTest(
        List[Value]([Identifier("ABC"), Identifier("OP1"), Value(123), 456]),
        "ABC,OP1,123,456",
    )
    test_expression_expr = FormatTest(
        Expression("123 {} TEST {}", [Identifier("OP1"), Identifier("OP2")]),
        "123 OP1 TEST OP2",
    )
    test_identifier = FormatTest(
        Identifier("keyword"),
        "keyword",
    )
    test_is_in_list = FormatTest(
        IsIn(Identifier("keyword"), [1, 2, 3]),
        "keyword IN (1,2,3)",
    )
    test_is_in_negate = FormatTest(
        IsIn(Identifier("keyword"), [1, 2, 3], negate=True),
        "keyword NOT IN (1,2,3)",
    )
    test_is_in_iterable = FormatTest(
        IsIn(Identifier("keyword"), "ABC"),
        "keyword IN ('A','B','C')",
    )
    test_is_in_query = FormatTest(
        IsIn(Identifier("keyword"), Query("table").columns("p")),
        "keyword IN (SELECT p FROM table)",
    )
    test_chain_functions = FormatTest(
        Identifier("OP1").is_in("ABC").is_in("ABC"),
        "OP1 IN ('A','B','C') IN ('A','B','C')",
    )
    test_chain_functions_operators = FormatTest(
        Identifier("OP1").is_in("ABC").is_in("ABC") & (Identifier("OP2") != 3),
        "OP1 IN ('A','B','C') IN ('A','B','C') AND OP2!=3",
    )
    test_cast = FormatTest(
        Cast("x", "VARCHAR(100)"),
        "CAST(x AS VARCHAR(100))",
    )
    test_substring = FormatTest(
        Substring("x", 11, 3),
        "SUBSTRING(x,12,3)",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "SUBSTRING(x,2)",
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
        "ORD(x)",
    )
    test_ord_texttype = FormatTest(
        Ord(Identifier("x", type=TextType())),
        "ORD(x)",
    )
    test_hex_texttype = FormatTest(
        Hex(Identifier("A", type=TextType())),
        "HEX(A)",
    )
    test_hex_blobtype = FormatTest(
        Hex(Identifier("A", type=BlobType())),
        "HEX(A)",
    )

    # ConcatWS

    test_concatws_several_args = FormatTest(
        ConcatWS(":", "ABC"),
        "CONCAT('A',':','B',':','C')",
    )
    test_concatws_no_args = FormatTest(
        ConcatWS(":", []),
        "''",
    )
    test_concatws_single_arg = FormatTest(
        ConcatWS(":", ["A"]),
        "'A'",
    )

    # Function

    test_function_simple_call = FormatTest(
        Function("CALL", (Identifier("OP1"), Identifier("OP2"))),
        "CALL(OP1,OP2)",
    )
    test_function_with_parameter_parenthesis = FormatTest(
        Function("CALL", (Identifier("OP1"), combination())),
        "CALL(OP1,(OP1<=OP2 AND OP1>=OP2))",
    )

    # Limit

    test_limit = FormatTest(
        Limit(5, 10),
        "LIMIT 5,10",
    )
    test_limit_with_start_at_0 = FormatTest(
        Limit(0, 10),
        "LIMIT 10",
    )

    # Serialisation

    test_serialize_texttype_value = FormatTest(
        Expr("E", type=TextType()),
        "E",
        serialized=True,
    )
    test_serialize_inttype_value = FormatTest(
        Expr("E", type=IntType()),
        "CAST(E AS TEXT)",
        serialized=True,
    )
    test_serialize_booltype_value = FormatTest(
        Expr("E", type=BoolType()),
        "CAST(E AS TEXT)",
        serialized=True,
    )
    test_serialize_blobtype_value = FormatTest(
        Expr("E", type=BlobType()),
        "HEX(E)",
        serialized=True,
    )

    def test_build_not_a_node(self) -> None:
        with self.assertRaisesRegex(TypeError, "Expected Node, got str"):
            self.compiler.compile("Not a node ...", "")

    def test_expression_missing_arg(self):
        with self.assertRaises(KeyError):
            self.compiler.compile(Expression("123 {x} TEST {}", [1]))

    def test_count_multiple_raises_exception(self):
        with self.assertRaises(NodeTypeError):
            Count(("test", "something"))

    def test_unexisting_method(self):
        c = Comparison("A", "=", "B")
        with self.assertRaises(AttributeError):
            c.does_not_exist

    # Serialization types

    def test_serialize_texttype_type(self):
        node = Expr("E", type=TextType())
        self.assertIs(self.compiler.serialize(node), node)

    def test_serialize_inttype_type(self):
        node = Expr("E", type=IntType())
        serialized = self.compiler.serialize(node)
        self.assertIsInstance(serialized.metadata.type, TextType)
        self.assertEqual(serialized.metadata.type.charset, "1234567890")
        self.assertIs(serialized.metadata.type.size.min, 0)
        self.assertIsNone(serialized.metadata.type.size.max)

    def test_serialize_booltype_type(self):
        node = Expr("E", type=BoolType())
        serialized = self.compiler.serialize(node)
        self.assertIsInstance(serialized.metadata.type, TextType)
        self.assertEqual(serialized.metadata.type.charset, "01")
        self.assertIs(serialized.metadata.type.size.min, 1)
        self.assertIs(serialized.metadata.type.size.max, 1)

    def test_serialize_unknowntype_raises_exception(self):
        node = Expr("E", type=UnknownType())
        with self.assertRaises(ConversionError) as cm:
            self.compiler.serialize(node)
        self.assertEqual(str(cm.exception), "Cannot serialize type: UnknownType")

    # Deserialization

    def test_deserialize_texttype(self):
        node = Expr("E", type=TextType())
        self.assertEqual(self.compiler.deserialize(node.metadata.type, b"123"), "123")

    def test_deserialize_booltype(self):
        node = Expr("E", type=BoolType())
        self.assertIs(self.compiler.deserialize(node.metadata.type, b"1"), True)
        self.assertIs(self.compiler.deserialize(node.metadata.type, b"0"), False)

    def test_deserialize_booltype_with_invalid_data_raises_conversionerror(self):
        node = Expr("E", type=BoolType())

        with self.assertRaises(ConversionError) as cm:
            self.compiler.deserialize(node.metadata.type, b"something")

        self.assertEqual(str(cm.exception), "Invalid boolean value: b'something'")

    def test_deserialize_inttype_with_invalid_data_raises_conversionerror(self):
        node = Expr("E", type=IntType())

        with self.assertRaises(ConversionError) as cm:
            self.compiler.deserialize(node.metadata.type, b"something")

        self.assertEqual(str(cm.exception), "Invalid integer value: b'something'")

    def test_deserialize_blobtype_with_invalid_data_raises_conversionerror(self):
        node = Expr("E", type=BlobType())

        with self.assertRaises(ConversionError) as cm:
            self.compiler.deserialize(node.metadata.type, b"this is not an hexa string")

        self.assertEqual(
            str(cm.exception),
            "Invalid hexadecimal value: b'this is not an hexa string'",
        )

    def test_deserialize_inttype(self):
        node = Expr("E", type=IntType())
        self.assertIs(self.compiler.deserialize(node.metadata.type, b"123"), 123)

    def test_deserialize_unknowntype_raises_exception(self):
        node = Expr("E", type=UnknownType())
        with self.assertRaises(ConversionError) as cm:
            self.compiler.deserialize(node.metadata.type, b"test")
        self.assertEqual(str(cm.exception), "Cannot deserialize type: UnknownType")

    def test_deserialize_invalid_type(self):
        node = Expr("E", type="this is not a type")
        with self.assertRaises(ConversionError) as cm:
            self.compiler.deserialize(node.metadata.type, b"test")
        self.assertEqual(str(cm.exception), "Cannot deserialize type: str")

    # Adjustments

    def test_adjust_column_of_standard_types_is_identity(self):
        types = [
            BoolType(),
            IntType(),
            TextType(),
            BlobType(),
        ]
        for type in types:
            column = Identifier("A", type=type)
            self.assertIs(self.compiler._adjust_column(column), column)

    def test_adjust_column_of_unknown_type_is_modified(self):
        # The default compiler produces no changes
        column = Identifier("a", type=UnknownType())
        column = self.compiler._adjust_column(column)
        self.assertFormat(column, "a")


class MockConcatWSCompiler(HasConcatWSMixin, MockCompiler):
    pass


class TestConcatWSMixin:
    """Change tests for ConcatWS to match the expected output of the compiler that uses
    the `HasConcatWSMixin`.
    """

    compiler_class = MockConcatWSCompiler

    test_concatws_no_args = FormatTest(
        ConcatWS(":", []),
        "''",
    )
    test_concatws_one_arg = FormatTest(
        ConcatWS(":", ["A"]),
        "'A'",
    )
    test_concatws_several_args = FormatTest(
        ConcatWS(":", "ABC"),
        "CONCAT_WS(':','A','B','C')",
    )
