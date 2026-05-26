from collections import namedtuple
import glob
import os
import os.path
import tempfile
import unittest
from multiprocessing import Process
from typing import Any

from rich.table import Table

from ending.ast import BlobType, Identifier, Query
from ending.cli.misc import console
from ending.struct import parameterized, resultset, storable
from ending.struct.metadata import (
    MapState,
    State,
    Listener,
    QueryState,
    Context,
    Listener,
)
from ending.util.humanized_compiler import HumanizedCompiler

# Metadata


class TestListener(unittest.TestCase):
    def test_cannot_listen_twice(self):
        state = State()
        listener = Listener()
        listener.link(state)
        with self.assertRaises(RuntimeError):
            listener.link(state)

    def test_link_listener_cannot_be_called_directly(self):
        state = State()
        listener = Listener()
        with self.assertRaises(RuntimeError):
            state._link_listener(listener)


class TestContext(unittest.TestCase):
    def test_data_is_updated(self):
        state = State()
        old = Context(state, {"A": "A1"})
        new = old.with_(B="B2", C="C3")
        self.assertTrue(old.data, {"A": "A1"})
        self.assertTrue(new.data, {"A": "A1", "B": "B2", "C": "C2"})

    def test_common_methods_are_transmitted(self):
        tester = self

        class ReceivesContextState(State):
            def restore(self, key, context):
                tester.assertEqual(key, "get")
                tester.assertEqual(context, ctx)

        # First, test that it works normally
        state = State()
        ctx = Context(state, {"A": "A1"})
        ctx.restore("get")

        # Then, check that it is really transmitted to the state
        state = ReceivesContextState()
        ctx = Context(state, {"A": "A1"})
        ctx.restore("get")

    def test_tuple(self):
        state = State()
        ctx = Context(state, {"A": "A1", "B": "B2", "C": "C3"})
        tpl = ctx.tuple()

        self.assertEqual(tuple(tpl), ("A1", "B2", "C3"))
        self.assertEqual(tpl.A, "A1")
        self.assertEqual(tpl.B, "B2")
        self.assertEqual(tpl.C, "C3")


class TestQueryState(unittest.TestCase):
    def test_get_partial_results_no_parameters(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 4)
        state.results = {
            1: {0: "0", 1: "1", 2: "2"},
            3: {2: "N"},
        }
        results = state.get_partial_results()
        self.assertEqual(
            results.data,
            [
                [U, U, U],
                ["0", "1", "2"],
                [U, U, U],
                [U, U, "N"],
            ],
        )

    def test_get_partial_results_trimmed(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 5)
        state.results = {
            1: {0: "0", 1: "1", 2: "2"},
            3: {2: "N"},
        }
        results = state.get_partial_results(trim=True)
        self.assertEqual(
            results.data,
            [
                ["0", "1", "2"],
                [U, U, U],
                [U, U, "N"],
            ],
        )

    def test_get_partial_results_first(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 4)
        state.results = {
            1: {0: "0", 1: "1", 2: "2"},
            3: {2: "N"},
        }
        results = state.get_partial_results(first=2)
        self.assertEqual(
            results.data,
            [
                [U, U, U],
                ["0", "1", "2"],
            ],
        )

    def test_get_partial_results_first_trimmmed(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 5)
        state.results = {
            1: {0: "0", 1: "1", 2: "2"},
            3: {2: "N"},
        }
        results = state.get_partial_results(first=2, trim=True)
        self.assertEqual(
            results.data,
            [
                ["0", "1", "2"],
                [U, U, U],
            ],
        )

    def test_get_partial_results_last(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 4)
        state.results = {
            0: {0: "0", 1: "1", 2: "2"},
            2: {2: "N"},
        }
        results = state.get_partial_results(last=2)
        self.assertEqual(
            results.data,
            [
                [U, U, "N"],
                [U, U, U],
            ],
        )

    def test_get_partial_results_last_trimmmed(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 5)
        state.results = {
            1: {0: "0", 1: "1", 2: "2"},
            3: {2: "N"},
        }
        results = state.get_partial_results(last=2, trim=True)
        self.assertEqual(
            results.data,
            [
                [U, U, U],
                [U, U, "N"],
            ],
        )

    def test_get_partial_results_empty(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 4)
        state.results = {}
        results = state.get_partial_results()
        self.assertEqual(
            results.data,
            [
                [U, U, U],
                [U, U, U],
                [U, U, U],
                [U, U, U],
            ],
        )

    def test_get_partial_results_empty_trimmed(self):
        U = resultset.UnknownCell()
        state = QueryState()
        state.query = Query("test").columns("A", "B", "C")
        state.bounds = (0, 5)
        state.results = {}
        results = state.get_partial_results(trim=True)
        self.assertEqual(results.data, [])

    def test_get_partial_results_no_query(self):
        state = QueryState()
        results = state.get_partial_results()
        self.assertIsNone(results)
        state.query = Query("test").columns("A", "B", "C")
        results = state.get_partial_results()
        self.assertIsNone(results)
        state.bounds = (0, 5)
        results = state.get_partial_results()
        self.assertIsNotNone(results)


class TestMapState(unittest.TestCase):
    def test_add_reflected_in_map(self):
        state = MapState()
        ctx = state.context

        ctx.notify("dump:done", depth="databases", results=["db01", "db02"])
        self.assertEqual(
            state.map.items,
            {
                "db01": {},
                "db02": {},
            },
        )

        ctx.notify(
            "dump:done", depth="tables", database="db03", results=["tb01", "tb02"]
        )
        self.assertEqual(
            state.map.items,
            {
                "db01": {},
                "db02": {},
                "db03": {
                    "tb01": {},
                    "tb02": {},
                },
            },
        )

        ctx.notify(
            "dump:done",
            depth="columns",
            database="db03",
            table="tb01",
            results=["col01", "col02", "col03"],
        )
        self.assertEqual(
            state.map.items,
            {
                "db01": {},
                "db02": {},
                "db03": {
                    "tb01": {"col01": {}, "col02": {}, "col03": {}},
                    "tb02": {},
                },
            },
        )


class SomeParameterized(parameterized.Parameterized):
    pass


class TestParameterized(unittest.TestCase):
    def test_parametererror_details(self):
        exc = parameterized.ParameterError("param1", "details for error")
        self.assertEqual(
            str(exc),
            "parameter 'param1' is invalid: details for error",
        )

    def test_incorrect_type(self):
        test = SomeParameterized()

        with self.assertRaisesRegex(
            parameterized.ParameterError,
            r"parameter 'test' is invalid: type must be str, not int",
        ):
            test.check_argument_type("test", 3, (str,))

        with self.assertRaisesRegex(
            parameterized.ParameterError,
            r"parameter 'test' is invalid: type must be str, not int",
        ):
            test.check_argument_type("test", 3, str)

    def test_incorrect_types(self):
        test = SomeParameterized()

        with self.assertRaisesRegex(
            parameterized.ParameterError,
            r"parameter 'test' is invalid: type must be one of \(str, float\), not int",
        ):
            test.check_argument_type("test", 3, (str, float))

    def test_check_parameters_ok(self):
        test = SomeParameterized()

        test.check_parameters(
            {"a": 3, "b": "test", "c": None},
            required={"a": int, "b": (str, float)},
            optional={"c": int},
        )

    def test_check_parameters_invalid_type_for_required_parameter(self):
        test = SomeParameterized()

        with self.assertRaisesRegex(
            parameterized.ParameterError,
            r"parameter 'a' is invalid: type must be int, not str",
        ):
            test.check_parameters(
                {"a": "test", "b": "test", "c": None},
                required={"a": int, "b": (str, float)},
                optional={"c": int},
            )

    def test_check_parameters_invalid_type_for_optional_parameter(self):
        test = SomeParameterized()

        with self.assertRaisesRegex(
            parameterized.ParameterError,
            r"parameter 'c' is invalid: type must be int, not str",
        ):
            test.check_parameters(
                {"a": 3, "b": "test", "c": "test"},
                required={"a": int, "b": (str, float)},
                optional={"c": int},
            )


class TestStorable(unittest.TestCase):
    def test_store_creates_one_txt_file(self):
        class Test(storable.Storable):
            def __str__(self):
                return "ABC"

        obj = Test()

        with tempfile.TemporaryDirectory() as tmpdir:
            obj.store(os.path.join(tmpdir, "test"))
            files = glob.glob(os.path.join(tmpdir, "*"))
            self.assertEqual(len(files), 1)
            file = files.pop()
            self.assertEqual(file, os.path.join(tmpdir, "test.txt"))
            with open(file, "r") as h:
                self.assertEqual(h.read(), "ABC")

    def test_store_as_txt_creates_one_txt_file(self):
        class Test(storable.Storable):
            def __str__(self):
                return "ABC"

        obj = Test()

        with tempfile.TemporaryDirectory() as tmpdir:
            obj.store_as_txt(os.path.join(tmpdir, "test.txt"))
            files = glob.glob(os.path.join(tmpdir, "*"))
            self.assertEqual(len(files), 1)
            file = files.pop()
            self.assertEqual(file, os.path.join(tmpdir, "test.txt"))
            with open(file, "r") as h:
                self.assertEqual(h.read(), "ABC")

    def test_store_with_no_name_generates_a_name_and_stores_in_current_dir(self):
        class Test(storable.Storable):
            def __str__(self):
                return "ABC"

        obj = Test()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test")
            obj.store(path)
            path_txt = f"{path}.txt"
            self.assertTrue(os.path.exists(path_txt))
            with open(path_txt, "r") as h:
                self.assertEqual(h.read(), "ABC")


def _build_fake_resultset(results: list[list[Any]]) -> resultset.ResultSet:
    query = Query("users").columns(*([f"a{i}" for i in range(len(results[0]))]))
    compiler = HumanizedCompiler()
    compiler.wrap(query)
    return resultset.ResultSet(query, results)


def test_display(results) -> None:
    results = _build_fake_resultset(results)
    # The CLI only displays up to 1000 rows
    with console.capture():
        console.print(results.take(1000))


def test_store(results) -> None:
    results = _build_fake_resultset(results)
    results.store_as_txt("/dev/null")


class TestResultSet(unittest.TestCase):
    def test_string_output(self):
        self.maxDiff = None
        q = Query("users").columns("a", "b", "c")
        compiler = HumanizedCompiler()
        compiler.wrap(q)
        result_set = resultset.ResultSet(q, [["a1", "b1", "c1"], ["a2", "b2", "c2"]])
        string = str(result_set)
        self.assertEqual(
            string,
            """\
╭────┬────┬────╮
│ a  │ b  │ c  │
├────┼────┼────┤
│ a1 │ b1 │ c1 │
│ a2 │ b2 │ c2 │
╰────┴────┴────╯
""",
        )

    def test_store_as_csv(self):
        q = Query("users").columns("a", "b", "c")
        compiler = HumanizedCompiler()
        compiler.wrap(q)
        result_set = resultset.ResultSet(q, [["a1", "b1", "c1"], ["a2", "b2", "c2"]])

        named = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        named.close()

        result_set.store_as_csv(named.name)

        with open(named.name, "r") as handle:
            self.assertEqual(handle.read(), "a,b,c\na1,b1,c1\na2,b2,c2\n")

        os.unlink(named.name)

    def test_store_as_rich(self):
        q = Query("users").columns("a", "b", "c")
        compiler = HumanizedCompiler()
        compiler.wrap(q)
        result_set = resultset.ResultSet(q, [["a1", "b1", "c1"], ["a2", "b2", "c2"]])

        named = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        named.close()

        result_set.store_as_rich(named.name)

        self.assertTrue(os.path.exists(named.name))
        os.unlink(named.name)

    def test_store_as_csv_blobs_are_base64(self):
        q = Query("users").columns(Identifier("a", type=BlobType()))
        compiler = HumanizedCompiler()
        compiler.wrap(q)
        result_set = resultset.ResultSet(q, [[b"a1"], [b"a2"]])

        named = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        named.close()

        result_set.store_as_csv(named.name)

        with open(named.name, "r") as handle:
            self.assertEqual(handle.read(), "a\nYTE=\nYTI=\n")

        os.unlink(named.name)

    def test_store_results_for_huge_number_of_rows_is_not_slow(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(400_000)]
        TIMEOUT = 10

        process = Process(target=test_store, args=(results,))
        process.start()
        process.join(timeout=TIMEOUT)
        if process.is_alive():
            process.terminate()
            self.fail(f"Process is still alive after {TIMEOUT} seconds")

    def test_display_results_for_huge_number_of_rows_is_not_slow(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(400_000)]

        process = Process(target=test_display, args=(results,))
        process.start()
        process.join(timeout=5)

        if process.is_alive():
            process.terminate()
            self.fail("Process is still alive after 5 seconds")

    def test_store_results_for_huge_row_is_not_slow(self):
        results = [["A" * 1024**2 * 20]]

        process = Process(target=test_store, args=(results,))
        process.start()
        process.join(timeout=5)

        if process.is_alive():
            process.terminate()
            self.fail("Process is still alive after 5 seconds")

    def test_display_results_for_huge_row_is_not_slow(self):
        results = [["A" * 1024**2 * 20]]

        process = Process(target=test_display, args=(results,))
        process.start()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            self.fail("Process is still alive after 5 seconds")

    def test_take_returns_ok_number_of_results(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(20)]
        query = Query("users").columns(*(["a"] * 8))
        results = resultset.ResultSet(query, results)

        taken = results.take(10)
        self.assertEqual(len(taken.data), 10)

        taken = results.take(10, ellipsis=False)
        self.assertEqual(len(taken.data), 10)

    def test_take_returns_ellipsis_if_not_all_results_are_taken(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(20)]
        query = Query("users").columns(*(["a"] * 8))
        results = resultset.ResultSet(query, results)

        taken = results.take(10, ellipsis=True)
        self.assertEqual(len(taken.data), 10)
        self.assertEqual(taken.data[-1], [...] * 8)

    def test_column_returns_row(self):
        q = Query("users").columns(Identifier("a", type=BlobType()))
        compiler = HumanizedCompiler()
        compiler.wrap(q)
        result_set = resultset.ResultSet(q, [[b"a1", "a3"], [b"a2", "a4"]])
        column = result_set.column(0)
        self.assertEqual(column, [b"a1", b"a2"])
        column = result_set.column(1)
        self.assertEqual(column, ["a3", "a4"])

    def test_rich_returns_table(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(20)]
        query = Query("users").columns(*(["a"] * 8))
        compiler = HumanizedCompiler()
        compiler.wrap(query)
        results = resultset.ResultSet(query, results)
        table = results.__rich__()
        self.assertIsInstance(table, Table)

    def test_table_returns_table(self):
        results = [[f"{i}_{j}" for j in range(8)] for i in range(20)]
        query = Query("users").columns(*(["a"] * 8))
        compiler = HumanizedCompiler()
        compiler.wrap(query)
        results = resultset.ResultSet(query, results)
        table = results.table()
        self.assertIsInstance(table, Table)


class TestPartialCells(unittest.TestCase):
    def test_unknown_cell_string(self):
        unknown = resultset.UnknownCell()
        self.assertEqual(str(unknown), "?")

    def test_unknown_cell_rich(self):
        unknown = resultset.UnknownCell()
        rich_obj = unknown.__rich__()
        self.assertEqual(type(rich_obj).__name__, "Align")

    def test_partial_cell_bounds(self):
        cell = resultset.PartialCell(3)
        with self.assertRaises(IndexError):
            cell.set(-1, "a")
        with self.assertRaises(IndexError):
            cell.set(3, "a")

    def test_string_partial_cell_get_and_done(self):
        cell = resultset.StringPartialCell(4)
        self.assertEqual(cell.get(), "····")
        self.assertFalse(cell.done())

        cell.set(0, "a")
        cell.set(2, "c")
        self.assertEqual(cell.get(), "a·c·")
        self.assertFalse(cell.done())

        cell.set(1, "b")
        cell.set(3, "d")
        self.assertTrue(cell.done())
        self.assertEqual(cell.get(), "abcd")

    def test_string_partial_cell_matches(self):
        cell = resultset.StringPartialCell(3)
        cell.set(0, "a")
        cell.set(2, "c")

        self.assertTrue(cell.matches("abc"))
        self.assertFalse(cell.matches("abbb"))
        self.assertFalse(cell.matches("abd"))
        self.assertFalse(cell.matches("ab", prefix=False))
        self.assertTrue(cell.matches("abc", prefix=False))

    def test_bytes_partial_cell_get_and_matches(self):
        cell = resultset.BytesPartialCell(3)
        self.assertEqual(cell.get(), b"...")
        self.assertFalse(cell.done())

        cell.set(1, b"b")
        self.assertEqual(cell.get(), b".b.")
        self.assertFalse(cell.matches(b"ab", prefix=False))
        self.assertTrue(cell.matches(b"abb", prefix=False))
