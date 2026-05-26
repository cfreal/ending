import string
import typing
import unittest
from dataclasses import dataclass

from ending.ast import *
from ending.db.generic import Compiler
from ending.db.postgresql.api import ColonCast
from ending.exception import CompilerNotSetError, NodeTypeError
from ending.util import quoting
from tests.test_db_generic_compiler import MockCompiler


class TestNodeDecorator(unittest.TestCase):
    def test_node_decorator_can_only_be_used_on_nodes(self):
        class FakeNode:
            pass

        with self.assertRaises(TypeError) as cm:
            node(FakeNode)

        self.assertEqual(
            str(cm.exception), "node() can only be used on subclasses of Node"
        )


class TestNode(unittest.TestCase):
    def test_castable_node_from_correct_value_works(self):
        @node
        class SomeNode(CastableNode):
            a: str

        some_node = SomeNode.from_value("test")
        self.assertEqual(some_node.a, "test")

    def test_castabe_node_from_incorrect_value_raises_nodetypeerror(self):
        @node
        class SomeNode(CastableNode):
            a: str

        with self.assertRaises(NodeTypeError) as cm:
            SomeNode.from_value(3)

        self.assertEqual(str(cm.exception), "SomeNode.a: expected str, got 3 (int)")

    def test_castable_node_from_correct_value_works(self):
        @node
        class SomeNode(CastableNode):
            a: str

        original = Value("test")
        some_node = SomeNode.from_value(original)
        self.assertIs(some_node, original)

    def test_cast_standard_value_to_node(self):
        @node
        class FakeNode(Node):
            a: Identifier

        f = FakeNode(a="hello")

        self.assertIsInstance(f.a, Identifier)
        self.assertEqual(f.a.name, "hello")

    def test_does_not_cast_node_to_node(self):
        @node
        class FakeNode(Node):
            a: Value

        f = FakeNode(Identifier("hello"))

        self.assertIsInstance(f.a, Identifier)
        self.assertEqual(f.a.name, "hello")

    def test_instanciating_a_list_with_no_type_raises_typeerror(self):
        with self.assertRaises(TypeError) as cm:
            List([1, 2, 3])
        self.assertEqual(
            str(cm.exception),
            "List type not set: always instanciate lists with List[...]",
        )

    def test_that_compiler_is_forwarded_to_sub_items(self):
        @node
        class FakeNode(Node):
            a: Identifier
            b: Expression
            c: List[Value]

        f = FakeNode(
            a="a",
            b=Identifier("test"),
            c=[Value(1), Value(2)],
        )
        c = MockCompiler(quote=quoting.hexadecimal)
        c.wrap(f)
        self.assertEqual(f.__compiler__, c)
        self.assertEqual(f.a.__compiler__, c)
        self.assertEqual(f.b.__compiler__, c)
        self.assertEqual(f.c[0].__compiler__, c)
        self.assertEqual(f.c[1].__compiler__, c)

    def test_call_format_when_compiler_not_set(self):
        @node
        class FakeNode(Node):
            pass

        f = FakeNode()
        with self.assertRaises(CompilerNotSetError):
            format(f)

    def test_cannot_set_compiler_if_already_set(self):
        @node
        class FakeNode(Node):
            pass

        f = FakeNode()
        c1 = MockCompiler(quote=quoting.singlequote)
        c2 = MockCompiler(quote=quoting.singlequote)
        c1.wrap(f)
        c2.wrap(f)
        self.assertIs(f.__compiler__, c1)

    def test_repr(self):
        @node
        class FakeNode(Node):
            a: Identifier
            b: Expression

            def __format__(self, p):
                return "__str__CALL"

        self.assertEqual(
            repr(FakeNode(Value("a"), Value("b"))),
            "TestNode.test_repr.<locals>.FakeNode(a=Value(value='a'), b=Value(value='b'))",
        )

    def test_created_node_has_invalid_type_raises_exception(self):
        with self.assertRaises(NodeTypeError) as cm:

            @node
            class FakeNode(Node):
                a: Identifier
                b: list[int]

        self.assertEqual(
            str(cm.exception),
            "FakeNode.b: expected valid type, got list[int] (GenericAlias)",
        )

    def test_wrong_std_type_raises_typeerror(self):
        @node
        class FakeNode(Node):
            a: str
            b: int

        with self.assertRaisesRegex(
            TypeError, r"FakeNode.a: expected str, got \(1, 2\) \(tuple\)"
        ):
            FakeNode(a=(1, 2), b=3)

    def test_wrong_std_type_raises_typerror_deep(self):
        @node
        class FakeNode(Node):
            a: Identifier

        with self.assertRaisesRegex(
            TypeError, r"FakeNode.a: expected Identifier, got 3 \(int\)"
        ):
            repr(FakeNode(a=3))

    def test_comparison_shortcuts(self):
        a = Identifier("a")
        b = Identifier("b")
        self.assertEqual((a == b).operator, "=")
        self.assertEqual((a != b).operator, "!=")
        self.assertEqual((a <= b).operator, "<=")
        self.assertEqual((a < b).operator, "<")
        self.assertEqual((a >= b).operator, ">=")
        self.assertEqual((a > b).operator, ">")

    def test_operator_shortcuts(self):
        a = Identifier("a")
        b = Identifier("b")
        self.assertEqual((a & b).operator, "AND")
        self.assertEqual((a | b).operator, "OR")

    def test_functions_shortcuts(self):
        a = Identifier("a")
        self.assertIsInstance(a.is_in("123"), IsIn)
        self.assertIsInstance(a.like("123"), WordedComparison)
        self.assertEqual(a.like("123").operator, "LIKE")

    def test_str_format_with_compiler(self):
        v = Value(123)
        MockCompiler(quote=quoting.singlequote).wrap(v)
        self.assertEqual(format(v), "123")
        self.assertEqual(str(v), "123")

    def test_immutability(self):
        v = Value("a")
        with self.assertRaisesRegex(
            AttributeError, r"Metadata: Cannot set attribute 'type', object is frozen"
        ):
            v.metadata.type = 3

    def test_unhashable(self):
        with self.assertRaises(TypeError) as cm:
            v = {Value("a")}
        self.assertIn("Node objects are not hashable", str(cm.exception))

    def test_base_node_type_instanciation_raises_typeerror(self):
        with self.assertRaises(TypeError) as cm:
            v = Node.node_cast("something", Node)

    def test_base_node_type_cast_raises_nodetypeerror(self):
        @node
        class X(Node):
            y: Node

        with self.assertRaises(NodeTypeError) as cm:
            v = X("a")
        self.assertEqual(str(cm.exception), "X.y: expected Node, got 'a' (str)")

    def test_count_with_no_args_yields_star(self):
        count = Count()
        self.assertIsInstance(count.column, Star)

    def test_count_with_untyped_arg_yields_identifier(self):
        count = Count("a")
        self.assertIsInstance(count.column, Identifier)

    def test_alias_type_is_transfered_if_not_set(self):
        tpe = IntType()
        base = Identifier("a", type=tpe)
        alias = Identifier("b")
        self.assertIs(Alias(base, alias).alias.metadata.type, tpe)

    def test_alias_type_is_not_transfered_if_set(self):
        tpe = IntType()
        other_type = TextType()
        base = Identifier("a", type=tpe)
        alias = Identifier("b", type=other_type)
        self.assertIs(Alias(base, alias).alias.metadata.type, other_type)


class TestSingle(unittest.TestCase):
    def test_count_is_always_single(self):
        self.assertTrue(Count(Value(1)).metadata.single)
        self.assertTrue(Count(Identifier("a")).metadata.single)

    def test_inherits_single(self):
        node_types = [
            Ord,
            Length,
            Hex,
            lambda x: IsIn(x, (Value(1), Value(2))),
            lambda x: IsIn(Value(1), (Value(2), x)),
            lambda x: IsNull(x),
            lambda x: List[Value]((Value(1), x, Value(2))),
            lambda x: Cast(x, "varchar"),
            lambda x: Alias(x, "alias"),
            lambda x: Function["a"](Value("b"), x, Value("c")),
            lambda x: x & Value("3"),
            lambda x: x == Value("3"),
            lambda x: Concatenation((Value(1), x, Value(3))),
            lambda x: ConcatWS(Value("sep"), (x, Value(2))),
            lambda x: ConcatWS(x, (Value(1), Value(2))),
            lambda x: Query("a").columns(x),
        ]

        for NT in node_types:
            single = Identifier("single_identifier", single=True)
            not_single = Identifier("not_single_identifier", single=False)
            with_single = NT(single)
            with_not_single = NT(not_single)

            with self.subTest(repr(with_single)):
                self.assertTrue(
                    with_single.metadata.single,
                    f"{with_single!r} of single is not single",
                )
                self.assertFalse(
                    with_not_single.metadata.single,
                    f"{with_not_single!r} of not single is single",
                )

    def test_with_single_changes_single(self):
        v = Identifier("a")
        self.assertFalse(v.metadata.single)
        self.assertTrue(v.with_metadata(single=True).metadata.single)
        self.assertFalse(v.with_metadata(single=False).metadata.single)
        self.assertIsNot(v, v.with_metadata(single=False))
        self.assertIsNot(v, v.with_metadata(single=True))


class TestNodeWrappers(unittest.TestCase):
    def test_func_instead_of_function(self):
        f = Function["TEST"](1, 2)
        self.assertIsInstance(f, Function)
        self.assertEqual(f.name, "TEST")
        self.assertEqual(f.args[0].value, 1)
        self.assertEqual(f.args[1].value, 2)

    def test_expr_instead_of_expression(self):
        e = Expr("test {} {3}", 1, 2, 4)
        self.assertIsInstance(e, Expression)
        self.assertEqual(e.expression, "test {} {3}")
        self.assertEqual(e.args[0].value, 1)
        self.assertEqual(e.args[1].value, 2)


class TestNodeQuery(unittest.TestCase):
    def setUp(self):
        self.q = Query("t").columns("c")

    def test_distinct(self):
        self.assertTrue(self.q.distinct().q.distinct)
        self.assertFalse(self.q.distinct(False).q.distinct)

    def test_table(self):
        self.assertEqual(self.q.table("Z").q.table.name, "Z")

    def test_where_string(self):
        new_where = self.q.where("test{}", "A").q.where
        self.assertIsInstance(new_where, Expression)
        self.assertEqual(new_where.expression, "test{}")
        self.assertIsInstance(new_where.args[0], Value)
        self.assertEqual(new_where.args[0].value, "A")

    def test_where_node(self):
        new_where = self.q.where(Value(1)).q.where
        self.assertIsInstance(new_where, Value)
        self.assertEqual(new_where.value, 1)

    def test_order_none(self):
        self.assertIsNone(self.q.order("test").order(None).q.order)

    def test_order_str(self):
        q = self.q.order("test")
        self.assertIsInstance(q.q.order, List)
        self.assertIsInstance(q.q.order.items[0], Order)
        self.assertIsInstance(q.q.order.items[0].node, Identifier)
        self.assertEqual(q.q.order.items[0].node.name, "test")

    def test_order_identifier_reverse(self):
        orders = self.q.order(Identifier("a"), reverse=True).q.order
        self.assertIsInstance(orders, List)
        self.assertIsInstance(orders.items[0], Order)
        self.assertIsInstance(orders.items[0].node, Identifier)
        self.assertIs(orders.items[0].reverse, True)

    def test_order_identifier(self):
        orders = self.q.order(Identifier("a")).q.order
        self.assertIsInstance(orders, List)
        self.assertIsInstance(orders.items[0], Order)
        self.assertIsInstance(orders.items[0].node, Identifier)
        self.assertIs(orders.items[0].reverse, False)

    def test_adding_order_list_with_reverse_raises_assertion(self):
        with self.assertRaises(AssertionError) as cm:
            self.q.order(("a", "b"), reverse=True)
        self.assertEqual(
            str(cm.exception), "Cannot set reverse on list of order fields"
        )

    def test_adding_order_list_node_with_reverse_raises_assertion(self):
        with self.assertRaises(AssertionError) as cm:
            self.q.order(List[Order](("a", "b")), reverse=True)
        self.assertEqual(
            str(cm.exception), "Cannot set reverse on list of order fields"
        )

    def test_adding_order_as_order_node_with_reverse_raises_assertion(self):
        with self.assertRaises(AssertionError) as cm:
            self.q.order(Order("a"), reverse=True)
        self.assertEqual(
            str(cm.exception), "Cannot set reverse on order field of type Order"
        )

    def test_order_order(self):
        q = self.q.order(Order("test"))
        self.assertIsInstance(q.q.order, List)
        self.assertIsInstance(q.q.order.items[0], Order)
        self.assertIsInstance(q.q.order.items[0].node, Identifier)
        self.assertEqual(q.q.order.items[0].node.name, "test")

    def test_order_list(self):
        q = self.q.order(List[Value]([Order("test1"), Order("test2")]))
        self.assertIsInstance(q.q.order, List)
        self.assertIsInstance(q.q.order.items[0], Order)
        self.assertIsInstance(q.q.order.items[0].node, Identifier)
        self.assertEqual(q.q.order.items[0].node.name, "test1")
        self.assertIsInstance(q.q.order.items[1], Order)
        self.assertIsInstance(q.q.order.items[1].node, Identifier)
        self.assertEqual(q.q.order.items[1].node.name, "test2")

    def test_order_std_list(self):
        q = self.q.order([Order("test1"), Order("test2")])
        self.assertIsInstance(q.q.order, List)
        self.assertIsInstance(q.q.order.items[0], Order)
        self.assertIsInstance(q.q.order.items[0].node, Identifier)
        self.assertEqual(q.q.order.items[0].node.name, "test1")
        self.assertIsInstance(q.q.order.items[1], Order)
        self.assertIsInstance(q.q.order.items[1].node, Identifier)
        self.assertEqual(q.q.order.items[1].node.name, "test2")

    def test_limit_none(self):
        self.assertIsNone(self.q.limit(None).q.limit)

    def test_limit_with_limit_node(self):
        x = Limit(1, 2)
        self.assertIs(self.q.limit(x).q.limit, x)

    def test_limit_two(self):
        limit = self.q.limit(1, 2).q.limit
        self.assertIsInstance(limit, Limit)
        self.assertEqual(limit.start, 1)
        self.assertEqual(limit.count, 2)

    def test_limit_single_int(self):
        limit = self.q.limit(2).q.limit
        self.assertIsInstance(limit, Limit)
        self.assertEqual(limit.start, 0)
        self.assertEqual(limit.count, 2)

    def test_query_limit_tuple_crashes(self):
        b = Query("base").columns("a")

        with self.assertRaises(TypeError) as cm:
            b.limit((5, 10))

        self.assertEqual(str(cm.exception), "Invalid value for start: (5, 10)")

    def test_superquery_table_is_alias_of_query(self):
        b = Query("base").columns("a")
        s = b.super_query()
        ss = s.q.table

        self.assertIsInstance(ss, Alias)
        self.assertIsInstance(ss.node, Query)

    def test_superquery_removes_where_order_limit_and_distinct_from_superquery(self):
        b = Query("base").columns("a").where("1=1").order("A").limit(1).distinct(True)
        s = b.super_query()

        self.assertIsNone(s.q.where)
        self.assertIsNone(s.q.order)
        self.assertIsNone(s.q.limit)
        self.assertFalse(s.q.distinct)

    def test_superquery_keeps_normal_fields(self):
        id = Identifier("a")
        b = Query("base").columns(id)
        s = b.super_query()
        ss = s.q.table.node

        self.assertIsInstance(ss.q.columns[0], Identifier)
        self.assertIsInstance(s.q.columns[0], Identifier)
        self.assertEqual(s.q.columns[0].name, id.name)
        self.assertEqual(ss.q.columns[0].name, id.name)

    def test_superquery_aliases_other_fields(self):
        id = Expr("a")
        b = Query("base").columns(id)
        s = b.super_query()
        ss = s.q.table.node

        self.assertIsInstance(ss.q.columns[0], Alias)
        self.assertIsInstance(ss.q.columns[0].node, Expression)
        self.assertEqual(ss.q.columns[0].node.expression, id.expression)

        self.assertIsInstance(s.q.columns[0], Identifier)
        self.assertEqual(s.q.columns[0].name, ss.q.columns[0].alias)

    def test_superquery_does_not_alias_values_and_does_not_keep_them_in_the_subquery(
        self,
    ):
        # doesn't need alias nor presence in superquery
        va = Value("a")
        vb = Value("b")
        # doesn't need alias
        vc = Identifier("c")
        # needs alias
        vd = Expr("d")
        base = Query("base").columns(va, vb, vc, vd)
        s = base.super_query()
        ss = s.q.table.node

        self.assertEqual(len(ss.q.columns), 2)

        self.assertIsInstance(ss.q.columns[0], Identifier)
        self.assertEqual(ss.q.columns[0].name, "c")

        self.assertIsInstance(ss.q.columns[1], Alias)
        self.assertIsInstance(ss.q.columns[1].node, Expression)

        self.assertEqual(len(s.q.columns), 4)

        self.assertIsInstance(s.q.columns[0], Value)
        self.assertEqual(s.q.columns[0].value, "a")

        self.assertIsInstance(s.q.columns[1], Value)
        self.assertEqual(s.q.columns[1].value, "b")

        self.assertIsInstance(s.q.columns[2], Identifier)
        self.assertEqual(s.q.columns[2].name, "c")

        self.assertIsInstance(s.q.columns[3], Identifier)


class TestTyping(unittest.TestCase):
    def setUp(self):
        self.columns = [Value(c) for c in ("str", 123, None, b"test")]

    def test_value_bool(self):
        self.assertIsInstance(Value(True).metadata.type, BoolType)

    def test_value_str(self):
        self.assertIsInstance(Value("str").metadata.type, TextType)

    def test_value_bytes(self):
        self.assertIsInstance(Value(b"bytes").metadata.type, BlobType)

    def test_value_int(self):
        self.assertIsInstance(Value(123).metadata.type, IntType)

    def test_value_list(self):
        with self.assertRaises(ValueError) as cm:
            Value([]).metadata.type
        self.assertEqual(str(cm.exception), "Cannot determine SQL type for value []")

    def test_hex_type_of_blob(self):
        tpe = Hex(
            Identifier("test", type=BlobType(size=IntType(min=4, max=5)))
        ).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.charset, string.hexdigits)
        self.assertEqual(tpe.size.min, 2 * 4)
        self.assertEqual(tpe.size.max, 2 * 5)

    def test_hex_type_of_text(self):
        tpe = Hex(Identifier("test", type=TextType())).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.charset, string.hexdigits)
        self.assertEqual(tpe.size.min, 0)
        self.assertEqual(tpe.size.max, None)

    def test_ord_type_for_blob(self):
        tpe = Ord(Value("test", type=BlobType())).metadata.type
        self.assertIsInstance(tpe, IntType)
        self.assertEqual(tpe.min, 0)
        self.assertEqual(tpe.max, 255)

    def test_ord_type_for_text(self):
        tpe = Ord(Value("test", type=TextType())).metadata.type
        self.assertIsInstance(tpe, IntType)
        self.assertEqual(tpe.min, 0)
        self.assertIsNone(tpe.max)

    def test_concatws_has_text_type(self):
        self.assertIsInstance(
            ConcatWS(",", [Value("test"), Value("a")]).metadata.type, TextType
        )

    def test_concatenation_type(self):
        self.assertIsInstance(
            Concatenation((Value("test"), Value("a"))).metadata.type, TextType
        )

    def test_logicaloperation_type(self):
        self.assertIsInstance(
            LogicalOperation(Value("test"), "AND", Value("a")).metadata.type, BoolType
        )

    def test_isnull_type(self):
        self.assertIsInstance(IsNull(Identifier("test")).metadata.type, BoolType)

    def test_count_type(self):
        self.assertIsInstance(Count(Identifier("test")).metadata.type, IntType)

    def test_arithmeticoperation_type(self):
        self.assertIsInstance(
            ArithmeticOperation(Value("test"), "+", Value("a")).metadata.type, IntType
        )

    def test_booleanoperation_type(self):
        self.assertIsInstance(
            BooleanOperation(Value("test"), "=", Value("a")).metadata.type, BoolType
        )

    def test_comparison_type(self):
        self.assertIsInstance(
            Comparison(Identifier("test"), "=", Value("1")).metadata.type, BoolType
        )

    def test_default_type_is_unknown(self):
        @node
        class FakeNode(Node):
            a: str

        f = FakeNode("test")
        self.assertIsInstance(f.metadata.type, UnknownType)

    def test_type_charset_is_the_same(self):
        c = Value("123")
        self.assertEqual(c.metadata.type.charset, "123")

    def test_repr_IntType(self):
        t_int = IntType(min=1, max=3)
        self.assertEqual(repr(t_int), "IntType(min=1, max=3)")

    def test_repr_TextType(self):
        t_char = TextType(size=4)
        self.assertEqual(
            repr(t_char),
            "TextType(charset='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!\"#$%&\\'()*+,-./:;<=>?@[\\\\]^_`{|}~ \\t\\n\\r\\x0b\\x0c', size=IntType(min=4, max=4))",
        )

    def test_repr_BoolType(self):
        t_bool = BoolType()
        self.assertEqual(repr(t_bool), "BoolType()")

    def test_query_with_one_column_is_the_same_exact_type(self):
        for column in self.columns:
            with self.subTest(column=column):
                q = Query().columns(column)
                self.assertIs(q.metadata.type, column.metadata.type)

    def test_query_type_with_several_columns_is_unknown(self):
        q = Query().columns("a", "b")
        self.assertIsInstance(q.metadata.type, UnknownType)

    def test_substring_inherits_single_flag(self):
        single = Substring(Value("12"), 3, 4).metadata.single
        self.assertTrue(single)

    def test_substring_size_smaller_than_start(self):
        t = Substring(Value("12"), 3, 4).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 0)
        self.assertEqual(t.size.max, 0)

    def test_substring_size_smaller_than_stop(self):
        t = Substring(Value("123456"), 2, 10).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 4)
        self.assertEqual(t.size.max, 4)

    def test_substring_size_smaller_in_range(self):
        t = Substring(Value("123456789"), 2, 4).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 4)
        self.assertEqual(t.size.max, 4)

    def test_substring_count_not_set(self):
        t = Substring(Value("1234567890"), 2).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 10 - 2)
        self.assertEqual(t.size.max, 10 - 2)

    def test_substring_from_unknown_type(self):
        t = Substring(Identifier("something"), 2, 4).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 0)
        self.assertEqual(t.size.max, 4)

    def test_substring_from_unknown_type_without_count(self):
        t = Substring(Identifier("something"), 2).metadata.type
        self.assertIsInstance(t, TextType)
        self.assertIsInstance(t.size, IntType)
        self.assertEqual(t.size.min, 0)
        self.assertEqual(t.size.max, None)

    def test_not_metadata(self):
        self.assertIsInstance(Not(Value("a")).metadata.type, BoolType)
        self.assertTrue(Not(Value("a")).metadata.single)
        self.assertFalse(Not(Identifier("a")).metadata.single)

    def test_coloncast_metadata(self):
        self.assertIsInstance(ColonCast(Value("a"), "int").metadata.type, UnknownType)
        m = ColonCast(Value("a"), "int").metadata
        self.assertTrue(m.single)
        self.assertFalse(m.nullable)
        m = ColonCast(Identifier("a"), "int").metadata
        self.assertFalse(m.single)
        self.assertTrue(m.nullable)

    def test_case_singleness(self):
        single = Value("a", single=True)
        not_single = Value("b", single=False)

        for i in range(5):
            items = [single, single, single, single]
            items.insert(i, not_single)
            a, b, c, d, _ = items
            should_be_single = i == 4
            with self.subTest(
                f"Case",
                a=a.metadata.single,
                b=b.metadata.single,
                c=c.metadata.single,
                d=d.metadata.single,
                should_be_single=should_be_single,
            ):
                self.assertIs(Case(a, ((b, c),), d).metadata.single, should_be_single)

    def test_length(self):
        for column in self.columns:
            with self.subTest(column=column):
                q = Length(column)
                self.assertIsInstance(q.metadata.type, IntType)
                if isinstance(column.metadata.type, (BlobType, TextType)):
                    self.assertEqual(q.metadata.type.min, column.metadata.type.size.min)
                    self.assertEqual(q.metadata.type.max, column.metadata.type.size.max)
                else:
                    self.assertEqual(q.metadata.type.min, 0)
                    self.assertIsNone(q.metadata.type.max)

    def test_texttype_receives_string_charset(self):
        t = TextType(charset="test")
        self.assertEqual(t.charset, "tes")

    def test_alias_has_same_type_as_aliased(self):
        v = Identifier("column", type=TextType(charset="test"))
        self.assertIs(Alias(v, "test").metadata.type, v.metadata.type)

    def test_charset_has_no_duplicates_and_preserves_order(self):
        self.assertEqual(TextType(charset="abcdabdc").charset, "abcd")
        self.assertEqual(TextType(charset="abcdabdc").charset, "abcd")

    def test_charset_with_unknown_type_raises_typeerror(self):
        with self.assertRaisesRegex(
            TypeError, r"^TextType: charset should be str, not int$"
        ):
            TextType(charset=3)

    def test_byteset_with_unknown_type_raises_typeerror(self):
        with self.assertRaisesRegex(
            TypeError, r"^BlobType: byteset should be bytes, not int$"
        ):
            BlobType(byteset=3)

    def test_inttype_feedback_is_saved(self):
        tpe = IntType()
        tpe.feedback(123)
        self.assertEqual(tpe.previous_values, [123])

    def test_immutability(self):
        tpe = IntType()
        with self.assertRaisesRegex(
            AttributeError, r"IntType: Cannot set attribute 'min', object is frozen"
        ):
            tpe.min = 2

    def test_substring_of_unknown_has_unknown_size(self):
        tpe = Substring(Identifier("test"), 2).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.size.min, 0)
        self.assertIsNone(tpe.size.max)

    def test_substring_of_unknown_text_has_unknown_size(self):
        tpe = Substring(Identifier("test", type=TextType()), 2).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.size.min, 0)
        self.assertIsNone(tpe.size.max)

    def test_substring_of_unknown_text_with_length_has_unknown_size(self):
        tpe = Substring(Identifier("test", type=TextType()), 2, 4).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.size.min, 0)
        self.assertEqual(tpe.size.max, 4)

    def test_size_of_substring_of_unknown_text_with_min_size_none_is_zero(self):
        tpe = Substring(
            Identifier("test", type=TextType(size=IntType(None, None))), 2, 4
        ).metadata.type
        self.assertIsInstance(tpe, TextType)
        self.assertEqual(tpe.size.min, 0)

    def test_substring_of_blob_is_blob(self):
        tpe = Substring(Identifier("test", type=BlobType()), 2, 4).metadata.type
        self.assertIsInstance(tpe, BlobType)


class TestTypingToTextType(unittest.TestCase):
    def test_texttype(self):
        tpe = TextType()
        self.assertIs(tpe.to_texttype(), tpe)

    def test_unknowntype(self):
        tpe = UnknownType()
        cast = tpe.to_texttype()
        self.assertIsInstance(cast, TextType)

    def test_inttype(self):
        tpe = IntType()
        cast = tpe.to_texttype()
        self.assertIsInstance(cast, TextType)
        self.assertEqual(cast.charset, "1234567890")
        self.assertIs(cast.size.min, 0)
        self.assertIsNone(cast.size.max)

    def test_intype_with_value_zero_works(self):
        type = IntType(value=0)
        self.assertIs(type.min, 0)
        self.assertIs(type.max, 0)

    def test_booltype(self):
        tpe = BoolType()
        cast = tpe.to_texttype()
        self.assertIsInstance(cast, TextType)
        self.assertEqual(cast.charset, "01")
        self.assertIs(cast.size.min, 1)
        self.assertIs(cast.size.max, 1)

    def test_booltype_with_false_true(self):
        tpe = BoolType()
        cast = tpe.to_texttype(false="F", true="T")
        self.assertIsInstance(cast, TextType)
        self.assertEqual(cast.charset, "FT")
        self.assertIs(cast.size.min, 1)
        self.assertIs(cast.size.max, 1)

    def test_blobtype_with_no_size(self):
        tpe = BlobType()
        cast = tpe.to_texttype()
        self.assertIsInstance(cast, TextType)
        self.assertEqual(cast.charset, string.hexdigits)
        self.assertIs(cast.size.min, 0)
        self.assertIs(cast.size.max, None)

    def test_blobtype_with_size(self):
        tpe = BlobType(size=IntType(min=4, max=5))
        cast = tpe.to_texttype()
        self.assertIsInstance(cast, TextType)
        self.assertEqual(cast.charset, string.hexdigits)
        self.assertIs(cast.size.min, 4 * 2)
        self.assertIs(cast.size.max, 5 * 2)
