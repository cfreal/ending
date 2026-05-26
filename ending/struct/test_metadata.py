# python
import unittest

from ending.struct.metadata import QueryState, MapState, Context, Data, Nothing
from ending.struct.resultset import PartialCell
from ending.ast import Query


class TestQueryStateRestore(unittest.TestCase):
    def test_rows_success(self):
        state = QueryState()
        state.query = Query("t").columns("A", "B", "C")
        # provide complete rows for indexes 1 and 2
        state.results = {
            1: {0: "a1", 1: "b1", 2: "c1"},
            2: {0: "a2", 1: "b2", 2: "c2"},
        }
        ctx = Context(state, {"row": 1, "nb_rows": 2})
        res = state.restore("rows", ctx)
        self.assertIsInstance(res, Data)
        self.assertEqual(res.data, [["a1", "b1", "c1"], ["a2", "b2", "c2"]])

    def test_rows_missing_row(self):
        state = QueryState()
        state.query = Query("t").columns("A", "B")
        state.results = {
            1: {0: "a1", 1: "b1"},
            # row 2 missing
        }
        ctx = Context(state, {"row": 1, "nb_rows": 2})
        res = state.restore("rows", ctx)
        self.assertIs(res, Nothing)

    def test_rows_missing_column(self):
        state = QueryState()
        state.query = Query("t").columns("A", "B")
        state.results = {
            1: {0: "a1"},  # column 1 missing
            2: {0: "a2", 1: "b2"},
        }
        ctx = Context(state, {"row": 1, "nb_rows": 2})
        res = state.restore("rows", ctx)
        self.assertIs(res, Nothing)

    def test_rows_partial_cell(self):
        state = QueryState()
        state.query = Query("t").columns("A", "B")
        state.results = {
            1: {0: "a1", 1: PartialCell(5)},  # partial cell present
            2: {0: "a2", 1: "b2"},
        }
        ctx = Context(state, {"row": 1, "nb_rows": 2})
        res = state.restore("rows", ctx)
        self.assertIs(res, Nothing)

    def test_cell_success(self):
        state = QueryState()
        state.results = {3: {2: "value"}}
        ctx = Context(state, {"row": 3, "column": 2})
        res = state.restore("cell", ctx)
        self.assertIsInstance(res, Data)
        self.assertEqual(res.data, "value")

    def test_cell_missing(self):
        state = QueryState()
        state.results = {}
        ctx = Context(state, {"row": 0, "column": 0})
        res = state.restore("cell", ctx)
        self.assertIs(res, Nothing)

    def test_cell_partial(self):
        state = QueryState()
        state.results = {1: {0: PartialCell(10)}}
        ctx = Context(state, {"row": 1, "column": 0})
        res = state.restore("cell", ctx)
        self.assertIs(res, Nothing)


class TestMapStateRestore(unittest.TestCase):
    def setUp(self):
        self.state = MapState()
        # Prepare a simple nested structure:
        # db -> table -> column -> metadata dict (with "type")
        self.state.map.items = {
            "db01": {
                "tb01": {
                    "col01": {"type": "int"},
                    "col02": {"type": "text"},
                },
                "tb02": {},
            },
            "db02": {},
        }

    def test_non_part_key_returns_nothing(self):
        ctx = Context(self.state, {"depth": "databases"})
        res = self.state.restore("other", ctx)
        self.assertIs(res, Nothing)

    def test_databases_part(self):
        self.state.done.add(())
        ctx = Context(self.state, {"depth": "databases"})
        res = self.state.restore("part", ctx)
        self.assertIsInstance(res, Data)
        # order is not important; compare as sets
        self.assertEqual(set(res.data), {"db01", "db02"})

    def test_tables_part(self):
        self.state.done.add(("db01",))
        ctx = Context(self.state, {"depth": "tables", "database": "db01"})
        res = self.state.restore("part", ctx)
        self.assertIsInstance(res, Data)
        self.assertEqual(set(res.data), {"tb01", "tb02"})

    def test_columns_part(self):
        self.state.done.add(("db01", "tb01"))
        ctx = Context(
            self.state, {"depth": "columns", "database": "db01", "table": "tb01"}
        )
        res = self.state.restore("part", ctx)
        self.assertIsInstance(res, Data)
        self.assertEqual(set(res.data), {"col01", "col02"})

    def test_types_part(self):
        self.state.done.add(("db01", "tb01"))
        ctx = Context(
            self.state, {"depth": "types", "database": "db01", "table": "tb01"}
        )
        res = self.state.restore("part", ctx)
        self.assertIsInstance(res, Data)
        # The implementation returns a generator expression wrapped in Data; convert to list
        items = list(res.data)
        # Expect pairs [column, type]
        self.assertCountEqual(items, [["col01", "int"], ["col02", "text"]])
