import re
import unittest
from unittest import mock

import requests as base_requests
from rich.align import Align
from rich.text import Text

from ending.ast import (
    ConcatWS,
    Concatenation,
    Expr,
    Function,
    Identifier,
    IsIn,
    List,
    Node,
    Query,
    Substring,
    Value,
)
from ending.db import mysql
from ending.db.postgresql.api import ColonCast, Compiler
from ending.util import (
    freezable,
    humanized,
    humanized_compiler,
    logging,
    misc,
    polytomy,
    quoting,
    randomized,
    requests,
)
from tests import test_db_generic_compiler
from tests.test_db_generic_compiler import FormatTest, combination, base


class TestHumanizedCompiler(test_db_generic_compiler.TestCompiler):
    compiler_class = humanized_compiler.HumanizedCompiler
    compiler_args = {}

    test_query_column = FormatTest(
        Query().columns("col"),
        "SELECT col",
    )
    test_query_column_table = FormatTest(
        Query("table").columns("column"),
        "SELECT column FROM table",
    )
    test_query_columns_table = FormatTest(
        Query("table").columns("column2", "column3"),
        "SELECT column2, column3 FROM table",
    )
    test_query_where = FormatTest(
        Query("table").columns("column").where("c0={} AND c1={}"),
        "SELECT column FROM table\nWHERE c0={} AND c1={}",
    )
    test_query_where_expr = FormatTest(
        Query("table").columns("column").where("c0={} AND c1={}", 123, 456),
        "SELECT column FROM table\nWHERE c0=123 AND c1=456",
    )
    test_query_order_desc = FormatTest(
        Query("table").columns("column").order("test", True),
        "SELECT column FROM table ORDER BY test DESC",
    )
    test_query_order = FormatTest(
        Query("table").columns("column").order("test"),
        "SELECT column FROM table ORDER BY test",
    )
    test_query_limit_0_10 = FormatTest(
        Query("table").columns("column").limit(10),
        "SELECT column FROM table\nLIMIT 0, 10",
    )
    test_query_limit_5_10 = FormatTest(
        Query("table").columns("column").limit(5, 10),
        "SELECT column FROM table\nLIMIT 5, 10",
    )
    test_query_distinct = FormatTest(
        Query("table").columns("column").distinct(),
        "SELECT DISTINCT column FROM table",
    )
    test_query_parenthesized_simplify_no_table = FormatTest(
        Query().columns("column1"),
        "column1",
        formatter="p",
    )
    test_query_parenthesized_simplify_with_table = FormatTest(
        Query("table").columns("column"),
        "(SELECT column FROM table)",
        formatter="p",
    )
    test_query_reset_no_reset = FormatTest(
        Query("T").columns("C").where("1=1").order("C").limit(1, 1),
        "SELECT C FROM T\nWHERE 1=1 ORDER BY C\nLIMIT 1, 1",
    )
    test_query_several_columns = FormatTest(
        Query("t").columns("a", "b").limit(3, 4),
        "SELECT a, b FROM t\nLIMIT 3, 4",
    )
    test_list = FormatTest(
        List[Value]([Identifier("ABC"), Identifier("OP1"), Value(123), 456]),
        "ABC, OP1, 123, 456",
    )
    test_list_with_parentheses = FormatTest(
        List[Value](("a", "b", "c", "d")),
        "('a', 'b', 'c', 'd')",
        formatter="p",
    )

    test_chain_functions = FormatTest(
        Identifier("OP1").is_in("ABC").is_in("ABC"),
        "OP1 IN ('A', 'B', 'C') IN ('A', 'B', 'C')",
    )
    test_chain_functions_operators = FormatTest(
        Identifier("OP1").is_in("ABC").is_in("ABC") & (Identifier("OP2") != 3),
        "OP1 IN ('A', 'B', 'C') IN ('A', 'B', 'C') AND OP2!=3",
    )
    test_concatenation_several_args = FormatTest(
        Concatenation(["A", "B", "C", 123]),
        "CONCAT('A', 'B', 'C', 123)",
    )
    test_concatws_several_args = FormatTest(
        ConcatWS(":", "ABC"),
        "CONCAT('A', ':', 'B', ':', 'C')",
    )
    test_function_simple_call = FormatTest(
        Function("CALL", (Identifier("OP1"), Identifier("OP2"))),
        "CALL(OP1, OP2)",
    )
    test_function_with_parameter_parenthesis = FormatTest(
        Function("CALL", (Identifier("OP1"), combination())),
        "CALL(OP1, (OP1<=OP2 AND OP1>=OP2))",
    )
    test_is_in_list = FormatTest(
        IsIn(Identifier("keyword"), [1, 2, 3]),
        "keyword IN (1, 2, 3)",
    )
    test_is_in_negate = FormatTest(
        IsIn(Identifier("keyword"), [1, 2, 3], negate=True),
        "keyword NOT IN (1, 2, 3)",
    )
    test_is_in_iterable = FormatTest(
        IsIn(Identifier("keyword"), "ABC"),
        "keyword IN ('A', 'B', 'C')",
    )
    test_query_limit_no_table = FormatTest(
        Query().columns("A").limit(1),
        "SELECT A\nLIMIT 0, 1",
    )
    test_query_where_no_table = FormatTest(
        Query().columns("A").where("B"),
        "SELECT A\nWHERE B",
    )
    test_query_where_combination = FormatTest(
        base().where(combination()),
        "SELECT column FROM table\nWHERE OP1<=OP2 AND OP1>=OP2",
    )
    test_substring = FormatTest(
        Substring("x", 11, 3),
        "SUBSTRING(x, 12, 3)",
    )
    test_substring_no_length = FormatTest(
        Substring("x", 1),
        "SUBSTRING(x, 2)",
    )

    def skip_serialized(self):
        """This test is not relevant for humanized compiler, which does not serialize values.
        Skipping it.
        """

    def assertFormat(
        self, value, expected=None, regex=None, formatter="", serialized=False
    ):
        if serialized:
            return self.skip_serialized()
        return super().assertFormat(value, expected, regex, formatter, serialized)

    def test_value_str_hexadecimal(self):
        self.skipTest(
            "This test is not relevant for humanized compiler, which does not use hexadecimal quoting"
        )

    def test_adjust_column_of_standard_types_is_identity(self):
        self.assertRaises(RuntimeError, self.compiler._adjust_column, Identifier("a"))

    def test_adjust_column_of_unknown_type_is_modified(self):
        self.assertRaises(RuntimeError, self.compiler._adjust_column, Identifier("a"))

    test_serialize_unknowntype_raises_exception = skip_serialized
    test_serialize_texttype_type = skip_serialized
    test_serialize_blobtype_type = skip_serialized
    test_serialize_inttype_type = skip_serialized
    test_serialize_booltype_type = skip_serialized


class TestHumanized(unittest.TestCase):
    def test_node_works(self):
        self.assertEqual(
            humanized.node(Query("a").columns("b", "c")), "SELECT b, c FROM a"
        )

    def test_node_does_not_set_compiler_for_argument(self):
        q = Query("a").columns("b", "c")
        self.assertEqual(humanized.node(q), "SELECT b, c FROM a")
        self.assertIsNone(q.q.__compiler__)
        self.assertIsNone(q.q.columns[0].__compiler__)

    def test_node_ignores_compiler(self):
        q = Query("a").columns("b", "c")
        some_compiler = mysql.Compiler(quoting.singlequote)
        q._forward_compiler(some_compiler, force=True)
        self.assertEqual(humanized.node(q), "SELECT b, c FROM a")
        self.assertIs(q.q.__compiler__, some_compiler)
        self.assertIs(q.q.columns[0].__compiler__, some_compiler)

    def test_cell(self):
        self.assertEqual(humanized.cell(123), "123")
        self.assertEqual(humanized.cell(123.45), "123.45")
        self.assertEqual(humanized.cell("test"), "test")
        self.assertEqual(humanized.cell(b"test"), "test")
        self.assertEqual(humanized.cell(b"\xc3test"), "b'\\xc3test'")
        self.assertEqual(humanized.cell(...), "...")
        self.assertEqual(humanized.cell(Value(123)), "Value(value=123)")

    def test_rich_cell(self):
        value = Text("abc")
        converted = humanized.rich_cell(value)
        self.assertIs(converted, value)

        values_renderable = {
            True: "✓",
            False: "✕",
            None: "None",
            123: "123",
            123.45: "123.45",
            "test": "test",
            b"test": "test",
            b"\xc3test": "b'\\xc3test'",
            ...: "…",
            "A" * 3000: "A" * 999 + "…",
        }

        for value, expected in values_renderable.items():
            with self.subTest(value=value, expected=expected):
                converted = humanized.rich_cell(value)
                self.assertIsInstance(converted, (Align, Text))
                if isinstance(converted, Align):
                    self.assertEqual(converted.renderable, expected)
                else:
                    self.assertEqual(converted.plain, expected)

        with self.assertRaises(ValueError) as cm:
            humanized.rich_cell(object())

        self.assertRegex(
            str(cm.exception), r"Cannot render type object for <object object at .*>"
        )

    def test_size(self):
        self.assertEqual(humanized.size(9999), "9999")
        self.assertEqual(humanized.size(10000), "10.0K")
        self.assertEqual(humanized.size(13000), "13.0K")
        self.assertEqual(humanized.size(13400), "13.4K")
        self.assertEqual(humanized.size(13400000), "13.4M")

    def test_humanized_node_with_special_nodetype_and_no_compiler_returns_repr(self):
        node = ColonCast(Identifier("a"), "b")
        display = humanized.node(node)
        self.assertEqual(display, "ColonCast(node=Identifier(name='a'), cast='b')")

    def test_humanized_node_with_special_nodetype_and_a_compiler_returns_the_compilers_version(
        self,
    ):
        compiler = Compiler(quote=quoting.singlequote)
        node = ColonCast(Identifier("a"), "b")
        compiler.wrap(node)
        display = humanized.node(node)
        self.assertEqual(display, "a::b")

    def test_humanizedcompiler_does_not_serialize_or_adjust(self):
        compiler = humanized_compiler.HumanizedCompiler()
        identifier = Identifier("C")

        with self.assertRaisesRegex(RuntimeError, "This method should not get called"):
            compiler.serialize(identifier)

        with self.assertRaisesRegex(RuntimeError, "This method should not get called"):
            compiler._adjust_column(identifier)


class TestRandomized(unittest.TestCase):
    def test_different(self):
        self.assertNotEqual(randomized.string(100), randomized.string(100))
        self.assertNotEqual(randomized.alpha(100), randomized.alpha(100))
        self.assertNotEqual(randomized.hexa(100), randomized.hexa(100))
        self.assertNotEqual(randomized.lower(100), randomized.lower(100))

    def test_filename(self):
        self.assertIn("-", randomized.filename())
        self.assertIn(":", randomized.filename(":"))

    def test_identifier(self):
        value = randomized.identifier()
        self.assertNotIn(value, misc.SQL_KEYWORDS)
        self.assertEqual(len(value), 4)

    def test_tautology(self):
        value = randomized.tautology()
        match = re.match(r"^(\d+)=(\d+)$", value)
        if not match:
            self.fail(f"no match: {value!r}")
        self.assertEqual(match.group(1), match.group(2))

    def test_falsity(self):
        value = randomized.contradiction()
        match = re.match(r"^(\d+)=(\d+)$", value)
        if not match:
            self.fail(f"no match: {value!r}")
        self.assertNotEqual(match.group(1), match.group(2))

    def test_length_is_correct(self):
        self.assertEqual(len(randomized.string(100)), 100)

    def test_contradiction_gives_two_different_values(self):
        numbers = iter(["1", "1", "2"])

        def digits(n):
            return next(numbers)

        with mock.patch.object(randomized, "digits", digits):
            self.assertEqual(randomized.contradiction(), "1=2")


class TestQuoting(unittest.TestCase):
    def _test_for(self, func, input, output, single, empty):
        func = getattr(quoting, func)
        self.assertEqual(func(input), output)
        self.assertEqual(func("A"), single)
        self.assertEqual(func(""), empty)

    def test_singlequote(self):
        self._test_for("singlequote", "A'B", "'A''B'", "'A'", "''")

    def test_doublequote(self):
        self._test_for("doublequote", 'A"B', '"A""B"', '"A"', '""')

    def test_hexadecimal(self):
        self._test_for("hexadecimal", "ABC", "0x414243", "0x41", "TRIM(0x20)")

    def test_singlequote_backslash(self):
        self._test_for(
            "singlequote_backslash",
            "A\\B\nC\tD\rE\0F'G",
            "'A\\\\B\\nC\\tD\\rE\\0F\\'G'",
            "'A'",
            "''",
        )

    def test_doublequote_backslash(self):
        self._test_for(
            "doublequote_backslash",
            'A\\B\nC\tD\rE\0F"G',
            '"A\\\\B\\nC\\tD\\rE\\0F\\"G"',
            '"A"',
            '""',
        )

    def test_xstring(self):
        self._test_for("xstring", "ABC", "X'414243'", "X'41'", "X''")

    def test_sum_chr(self):
        self._test_for(
            "sum_chr", "ABC", "CHR(65)+CHR(66)+CHR(67)", "CHR(65)", "TRIM(CHR(32))"
        )

    def test_sum_char(self):
        self._test_for(
            "sum_char",
            "ABC",
            "CHAR(65)+CHAR(66)+CHAR(67)",
            "CHAR(65)",
            "TRIM(CHAR(32))",
        )

    def test_pipes_chr(self):
        self._test_for(
            "pipes_chr", "ABC", "CHR(65)|CHR(66)|CHR(67)", "CHR(65)", "TRIM(CHR(32))"
        )

    def test_pipes_char(self):
        self._test_for(
            "pipes_char",
            "ABC",
            "CHAR(65)|CHAR(66)|CHAR(67)",
            "CHAR(65)",
            "TRIM(CHAR(32))",
        )

    def test_dpipes_char(self):
        self._test_for(
            "dpipes_char",
            "ABC",
            "CHAR(65)||CHAR(66)||CHAR(67)",
            "CHAR(65)",
            "TRIM(CHAR(32))",
        )

    def test_dpipes_chr(self):
        self._test_for(
            "dpipes_chr", "ABC", "CHR(65)||CHR(66)||CHR(67)", "CHR(65)", "TRIM(CHR(32))"
        )

    def test_concat_chr(self):
        self._test_for(
            "concat_chr",
            "ABC",
            "CONCAT(CHR(65),CHR(66),CHR(67))",
            "CHR(65)",
            "TRIM(CHR(32))",
        )

    def test_concat_char(self):
        self._test_for(
            "concat_char",
            "ABC",
            "CONCAT(CHAR(65),CHAR(66),CHAR(67))",
            "CHAR(65)",
            "TRIM(CHAR(32))",
        )

    def test_char(self):
        self._test_for(
            "char",
            "ABC",
            "CHAR(65,66,67)",
            "CHAR(65)",
            "CHAR()",
        )

    def test_char_unicode(self):
        self._test_for(
            "char",
            "éèà",
            "CHAR(233,232,224)",
            "CHAR(65)",
            "CHAR()",
        )


class TestPolytomy(unittest.IsolatedAsyncioTestCase):
    async def test_polytomy_works(self):
        item_set = bytes(range(256))

        for nb_sections in range(2, 5):
            for item in item_set:

                async def check(section):
                    return item in section

                self.assertEqual(await polytomy.run(item_set, nb_sections, check), item)

    async def test_dichotomy_empty_set(self):
        full = bytes(range(0))
        X = ord("X")

        async def check(charset):
            return X in charset

        with self.assertRaises(polytomy.PolytomyNotInSetError):
            await polytomy.run(full, 2, check)

    async def test_dichotomy_one_item_not_ok(self):
        full = b"Y"
        X = ord("X")

        async def check(charset):
            return X in charset

        with self.assertRaises(polytomy.PolytomyNotInSetError):
            await polytomy.run(full, 2, check)

    async def test_dichotomy_one_item_ok(self):
        full = b"X"
        X = ord("X")

        async def check(charset):
            return X in charset

        result = await polytomy.run(full, 2, check)

        self.assertEqual(result, X)

    async def test_dichotomy_run_not_in_set(self):
        full = bytes(range(100))
        X = 110

        for nb_sections in range(2, 5):

            async def check(charset):
                return X in charset

            with self.assertRaises(polytomy.PolytomyNotInSetError):
                await polytomy.run(full, nb_sections, check)

    async def test_wrong_oracle_yields_a_several_results(self):
        full = bytes(range(128))
        X = 50

        for nb_sections in range(3, 5):
            with self.subTest(nb_sections=nb_sections):
                self._x = 50

                async def check(charset):
                    self._x = (self._x + 10) % 256
                    return self._x in charset

                with self.assertRaises(polytomy.PolytomyNotSingletonError) as cm:
                    await polytomy.run(full, nb_sections, check)
                self.assertEqual(
                    str(cm.exception), "Process did not yield one result: set()"
                )

    async def test_nb_sections_superior_to_number_of_items_works(self):
        full = bytes(range(128))
        X = 50

        async def check(charset):
            return X in charset

        result = await polytomy.run(full, 1000, check)
        self.assertEqual(result, X)

    async def test_nb_sections_inferior_to_two_raises(self):
        full = bytes(range(128))
        X = 50

        async def check(charset):
            return X in charset

        with self.assertRaises(AssertionError) as cm:
            await polytomy.run(full, 1, check)
        self.assertEqual(str(cm.exception), "nb_sections must be >= 2")

    async def test_polytomy_callable_receives_frozenset(self):
        full = bytes(range(128))
        X = 50

        async def check(charset):
            self.assertIsInstance(charset, frozenset)
            return X in charset

        result = await polytomy.run(full, 2, check)
        self.assertEqual(result, X)


class TestMisc(unittest.TestCase):
    def test_to_bytes(self):
        self.assertEqual(misc.to_bytes(3), b"3")
        self.assertEqual(misc.to_bytes(3.45), b"3.45")
        self.assertEqual(misc.to_bytes(b"test"), b"test")
        self.assertEqual(misc.to_bytes("test"), b"test")
        self.assertEqual(misc.to_bytes(memoryview(b"test")), b"test")

    def test_to_bytes_with_unknown_raises_exception(self):
        with self.assertRaises(TypeError) as cm:
            misc.to_bytes([1, 2, 3])
        self.assertEqual(str(cm.exception), "Cannot cast to bytes: [1, 2, 3]")

    def test_to_bytes_uses_default(self):
        self.assertEqual(misc.to_bytes("test", b"default"), b"test")
        self.assertEqual(misc.to_bytes(None, b"default"), b"default")

    def test_repr_attrs(self):
        class Obj:
            def __init__(self):
                self.toto = 3
                self.titi = "something"

        self.assertEqual(
            misc.repr_attrs(Obj(), ["toto", "titi"]), "Obj(toto=3, titi='something')"
        )

    def test_niter_n_negative(self):
        with self.assertRaisesRegex(
            ValueError, r"niter\(\): n needs to be strictly positive: -3"
        ):
            next(misc.niter(range(10), -3))

    def test_niter_n_3(self):
        self.assertEqual(
            list(misc.niter(range(10), 3)), [(0, 1, 2), (3, 4, 5), (6, 7, 8), (9,)]
        )

    def test_niter_n_1(self):
        self.assertEqual(
            list(misc.niter(range(10), 1)),
            [
                (0,),
                (1,),
                (2,),
                (3,),
                (4,),
                (5,),
                (6,),
                (7,),
                (8,),
                (9,),
            ],
        )

    def test_niter_bytes(self):
        self.assertEqual(list(misc.niter(b"abcdefg", 2)), [b"ab", b"cd", b"ef", b"g"])

    def test_niter_str(self):
        self.assertEqual(list(misc.niter("abcdefg", 2)), ["ab", "cd", "ef", "g"])


class TestLogging(unittest.TestCase):
    def test_set_level_calls_setLevel(self):
        # No point testing that the log file contains the proper data: it's
        # the original logging's module responsibility
        logging.set_level("DEBUG")
        self.assertEqual(
            logging.logging.getLevelName(
                logging.logging.getLogger().getEffectiveLevel()
            ),
            "DEBUG",
        )
        logging.set_level("INFO")
        self.assertEqual(
            logging.logging.getLevelName(
                logging.logging.getLogger().getEffectiveLevel()
            ),
            "INFO",
        )

    def test_define_log_level(self):
        with self.assertRaisesRegex(
            AttributeError, "SQL already defined in logging module"
        ):
            logging._define_log_level("SQL", 1000, "sql123")

        with self.assertRaisesRegex(
            AttributeError, "sql already defined in logging module"
        ):
            logging._define_log_level("NEW_TYPE", 1000, "sql")

        logging.logging.getLoggerClass().new_method = "fake method for exception"

        with self.assertRaisesRegex(
            AttributeError, "new_method already defined in logger class"
        ):
            logging._define_log_level("NEW_TYPE", 1000, "new_method")

        del logging.logging.getLoggerClass().new_method


class TestAsyncSession(unittest.IsolatedAsyncioTestCase):
    # TODO issue a real request
    async def test_send_one_request(self):
        session = requests.AsyncSession()
        task = session.request("GET", "https://localhost:1233/")

        try:
            await task
        except base_requests.exceptions.RequestException:
            return
        else:
            self.fail("Task did not raise exception")

    def test_close_session(self):
        session = requests.AsyncSession()
        session.close()


class TestFreezable(unittest.TestCase):
    def test_not_frozen(self):
        f = freezable.Freezable()

        try:
            f.b = 3
        except:
            self.fail(
                "Exception raised when setting attribute while object is not frozen"
            )

    def test_frozen(self):
        f = freezable.Freezable()
        f._freeze()
        with self.assertRaisesRegex(
            AttributeError, "Freezable: Cannot set attribute 'a', object is frozen"
        ):
            f.a = 3

    def test_freeze_twice_ok(self):
        f = freezable.Freezable()
        f._freeze()
        f._freeze()

    def test_unfreeze_twice_ok(self):
        f = freezable.Freezable()
        f._unfreeze()
        f._unfreeze()

    def test_unfreeze_not_frozen(self):
        f = freezable.Freezable()
        f._freeze()
        f._unfreeze()

        try:
            f.b = 3
        except:
            self.fail(
                "Exception raised when setting attribute while object is not frozen"
            )
