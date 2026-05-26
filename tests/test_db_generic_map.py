import os
import pathlib
import sqlite3
import tempfile
from asyncio import CancelledError
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from rich.console import Console

from ending.db import sqlite
from ending.db.generic.map import Map, MetadataMapper
from ending.util import quoting
from ending.util.misc import to_bytes


class TestMap(TestCase):
    def setUp(self) -> None:
        self.maxDiff = None
        self.items = {}
        for db in ["a", "b"]:
            sdb = self.items[f"db_{db}"] = {}
            for i in range(3):
                table = f"table_{db}_{i:02d}"
                stable = sdb[table] = {}
                for j in range(3):
                    column = f"column_{db}_{i:02d}_{j:02d}"
                    stable[column] = {}
        self.items["empty_db"] = {}
        self.items["single_table_db"] = {"empty_table": {}}
        self.items["db_a"]["table_a_00"]["column_a_00_00"] = {"type": "int"}
        self.items["db_a"]["table_a_00"]["column_a_00_01"] = {"type": "varchar"}
        self.items["db_a"]["table_a_00"]["column_a_00_02"] = {"type": "bool"}
        self.map = Map(self.items)

    def test_map_str(self):
        self.assertEqual(
            str(self.map),
            "db_a\n├── table_a_00\n│   ├── column_a_00_00 [int]\n│   ├── column_a_00_01 [varchar]\n│   └── column_a_00_02 [bool]\n├── table_a_01\n│   ├── column_a_01_00\n│   ├── column_a_01_01\n│   └── column_a_01_02\n└── table_a_02\n    ├── column_a_02_00\n    ├── column_a_02_01\n    └── column_a_02_02\ndb_b\n├── table_b_00\n│   ├── column_b_00_00\n│   ├── column_b_00_01\n│   └── column_b_00_02\n├── table_b_01\n│   ├── column_b_01_00\n│   ├── column_b_01_01\n│   └── column_b_01_02\n└── table_b_02\n    ├── column_b_02_00\n    ├── column_b_02_01\n    └── column_b_02_02\nempty_db\nsingle_table_db\n└── empty_table\n",
        )

    def test_map_csv(self):
        with tempfile.NamedTemporaryFile("a+") as handle:
            self.map.store_as_csv(handle.name)
            handle.seek(0)
            csv = handle.read()
            self.assertEqual(
                csv,
                """\
db,table,column,type
db_a,table_a_00,column_a_00_00,int
db_a,table_a_00,column_a_00_01,varchar
db_a,table_a_00,column_a_00_02,bool
db_a,table_a_01,column_a_01_00
db_a,table_a_01,column_a_01_01
db_a,table_a_01,column_a_01_02
db_a,table_a_02,column_a_02_00
db_a,table_a_02,column_a_02_01
db_a,table_a_02,column_a_02_02
db_b,table_b_00,column_b_00_00
db_b,table_b_00,column_b_00_01
db_b,table_b_00,column_b_00_02
db_b,table_b_01,column_b_01_00
db_b,table_b_01,column_b_01_01
db_b,table_b_01,column_b_01_02
db_b,table_b_02,column_b_02_00
db_b,table_b_02,column_b_02_01
db_b,table_b_02,column_b_02_02
empty_db
single_table_db,empty_table
""",
            )

    def test_add_database(self):
        self.map.add("test_db")
        self.assertEqual(self.items["test_db"], {})

    def test_add_table(self):
        self.map.add("test_db", "test_table")
        self.assertEqual(self.items["test_db"]["test_table"], {})

    def test_add_column(self):
        self.map.add("test_db", "test_table", "test_column")
        self.assertEqual(self.items["test_db"]["test_table"]["test_column"], {})

    def test_add_type(self):
        self.map.add("test_db", "test_table", "test_column", "test_type")
        self.assertEqual(
            self.items["test_db"]["test_table"]["test_column"], {"type": "test_type"}
        )

    def test_add_on_existing_does_not_overwrite(self):
        self.map.add("test_db", "test_table", "test_column")
        self.map.add("test_db", "test_table")
        self.assertEqual(self.items["test_db"]["test_table"]["test_column"], {})

    def test_add_other_does_not_overwrite(self):
        self.map.add("test_db", "test_table1", "test_column")
        self.map.add("test_db", "test_table2")
        self.assertEqual(self.items["test_db"]["test_table1"]["test_column"], {})
        self.assertEqual(self.items["test_db"]["test_table2"], {})

    def test_update_with_overwrite(self):
        self.map.update(
            Map(
                {
                    "db_a": {"table_a_04": {}},
                    "db_c": {"table_c_01": {"column_c_01_01": {}}},
                }
            )
        )
        self.assertEqual(
            self.map.items,
            {
                "db_a": {
                    "table_a_00": {
                        "column_a_00_00": {"type": "int"},
                        "column_a_00_01": {"type": "varchar"},
                        "column_a_00_02": {"type": "bool"},
                    },
                    "table_a_01": {
                        "column_a_01_00": {},
                        "column_a_01_01": {},
                        "column_a_01_02": {},
                    },
                    "table_a_02": {
                        "column_a_02_00": {},
                        "column_a_02_01": {},
                        "column_a_02_02": {},
                    },
                    "table_a_04": {},
                },
                "db_b": {
                    "table_b_00": {
                        "column_b_00_00": {},
                        "column_b_00_01": {},
                        "column_b_00_02": {},
                    },
                    "table_b_01": {
                        "column_b_01_00": {},
                        "column_b_01_01": {},
                        "column_b_01_02": {},
                    },
                    "table_b_02": {
                        "column_b_02_00": {},
                        "column_b_02_01": {},
                        "column_b_02_02": {},
                    },
                },
                "db_c": {"table_c_01": {"column_c_01_01": {}}},
                "empty_db": {},
                "single_table_db": {"empty_table": {}},
            },
        )

    def test_load_from_csv(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as handle:
            handle.write(
                """\
db,table,column,type
db_a,table_a_00,column_a_00_00,int
db_a,table_a_00,column_a_00_01,varchar
db_a,table_a_00,column_a_00_02,bool
db_a,table_a_01,column_a_01_00
db_a,table_a_01,column_a_01_01
db_a,table_a_01,column_a_01_02
db_a,table_a_02,column_a_02_00
db_a,table_a_02,column_a_02_01
db_a,table_a_02,column_a_02_02
db_b,table_b_00,column_b_00_00
db_b,table_b_00,column_b_00_01
db_b,table_b_00,column_b_00_02
db_b,table_b_01,column_b_01_00
db_b,table_b_01,column_b_01_01
db_b,table_b_01,column_b_01_02
db_b,table_b_02,column_b_02_00
db_b,table_b_02,column_b_02_01
db_b,table_b_02,column_b_02_02
empty_db
single_table_db,empty_table
"""
            )
        try:
            map = Map.load(handle.name)
            self.assertEqual(map.items, self.items)
        finally:
            os.unlink(handle.name)

    def test_filter(self):
        self.assertEqual(
            self.map.filter("databases").items,
            {"db_a": {}, "db_b": {}, "empty_db": {}, "single_table_db": {}},
        )
        self.assertEqual(
            self.map.filter("tables", column="column_*_02").items,
            {
                "db_a": {"table_a_00": {}, "table_a_01": {}, "table_a_02": {}},
                "db_b": {"table_b_00": {}, "table_b_01": {}, "table_b_02": {}},
            },
        )

    def test_filter_with_several_filters_returns_only_match(self):
        self.assertEqual(
            self.map.filter(
                "tables", database="db_a", table="table_*_01", column="column_*_01"
            ).items,
            {"db_a": {"table_a_01": {}}},
        )

    def test_filter_with_invalid_depth_raises_typerror(self):
        with self.assertRaises(TypeError) as cm:
            self.map.filter("INVALID")
        self.assertEqual(str(cm.exception), "Unknown depth: INVALID")

    def test_rich_compatible(self):
        console = Console(width=1024**2, force_terminal=False, emoji=False)
        with console.capture() as capture:
            console.print(self.map)
        tree = capture.get()
        self.assertEqual(
            tree,
            "◼ db_a\n├── ◼ table_a_00\n│   ├── ◼ column_a_00_00 int\n│   ├── ◼ column_a_00_01 varchar\n│   └── ◼ column_a_00_02 bool\n├── ◼ table_a_01\n│   ├── ◼ column_a_01_00\n│   ├── ◼ column_a_01_01\n│   └── ◼ column_a_01_02\n└── ◼ table_a_02\n    ├── ◼ column_a_02_00\n    ├── ◼ column_a_02_01\n    └── ◼ column_a_02_02\n◼ db_b\n├── ◼ table_b_00\n│   ├── ◼ column_b_00_00\n│   ├── ◼ column_b_00_01\n│   └── ◼ column_b_00_02\n├── ◼ table_b_01\n│   ├── ◼ column_b_01_00\n│   ├── ◼ column_b_01_01\n│   └── ◼ column_b_01_02\n└── ◼ table_b_02\n    ├── ◼ column_b_02_00\n    ├── ◼ column_b_02_01\n    └── ◼ column_b_02_02\n◼ empty_db\n◼ single_table_db\n└── ◼ empty_table\n",
        )

    def test_filter_with_no_match_returns_nothing(self):
        self.assertEqual(self.map.filter("databases", column="doesnotexist").items, {})
        self.assertEqual(self.map.filter("tables", column="doesnotexist").items, {})
        self.assertEqual(self.map.filter("columns", column="doesnotexist").items, {})

    def test_filter_columns_with_no_filter_returns_all(self):
        self.assertEqual(self.map.filter("types").items, self.map.items)

    def test_add_type_adds_type_key(self):
        self.map.add("test_db", "test_table", "test_column", "test_type")
        self.assertEqual(
            self.items["test_db"]["test_table"]["test_column"]["type"], "test_type"
        )


class TestMapper(IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = str(pathlib.Path(__file__).parent / "fixtures" / "schema.sqlite3")

    def setUp(self) -> None:
        self.maxDiff = None
        self.cnx = sqlite3.connect(self.db)
        self.cursor = self.cnx.cursor()
        compiler = sqlite.Compiler(quote=quoting.singlequote)
        method = sqlite.SelectMethod(
            compiler=compiler, inject=self.inject, nb_rows=1, columns=1, column=0
        )
        self.mapper = MetadataMapper(method)

        return super().setUp()

    def tearDown(self) -> None:
        self.cursor.close()
        self.cnx.close()
        return super().tearDown()

    async def inject(self, query):
        query = str(query)
        query = query.replace("information_schema.", "information_schema__")

        try:
            self.cursor.execute(query)
        except sqlite3.DatabaseError as e:
            return to_bytes(str(e))
        return b"".join(to_bytes(item) for row in self.cursor for item in row)

    async def test_simplemapper_fetch_db(self):
        map = await self.mapper.fetch("databases")
        self.assertEqual(map.items, {"db01": {}, "db02": {}})

    async def test_simplemapper_fetch_db_with_db_filter(self):
        map = await self.mapper.fetch("databases", database="db?2")
        self.assertEqual(map.items, {"db02": {}})

    async def test_simplemapper_fetch_db_with_column_filter(self):
        map = await self.mapper.fetch("databases", column="col?2")
        self.assertEqual(map.items, {"db01": {}, "db02": {}})

    async def test_simplemapper_fetch_columns_for_precise_db(self):
        map = await self.mapper.fetch("columns", database="db01")
        self.assertEqual(
            map.items,
            {
                "db01": {
                    "tbl01": {"col01": {}, "col02": {}, "col03": {}},
                    "tbl02": {"col01": {}, "col02": {}, "col03": {}},
                    "tbl03": {"col01": {}, "col02": {}, "col03": {}},
                }
            },
        )

    async def test_simplemapper_fetch_columns_for_unknown_db(self):
        map = await self.mapper.fetch("columns", database="db_which_does_not_exist")
        self.assertEqual(
            map.items,
            {},
        )

    async def test_simplemapper_fetch_perfect_filters(self):
        map = await self.mapper.fetch("tables", database="db_a", table="table_a_00")
        self.assertEqual(
            map.items,
            {},
        )

    async def test_simplemapper_fetch_types(self):
        map = await self.mapper.fetch("types")
        self.assertEqual(
            map.items,
            {
                "db01": {
                    "tbl01": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                    "tbl02": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                    "tbl03": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                },
                "db02": {
                    "tbl01": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                    "tbl02": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                    "tbl03": {
                        "col01": {"type": "INTEGER"},
                        "col02": {"type": "TEXT"},
                        "col03": {"type": "BOOLEAN"},
                    },
                },
            },
        )

    async def test_internal_map_cumulates_maps(self):
        mapper = MetadataMapper(self.mapper.method)
        x = await mapper.fetch("columns", database="db02")
        y = await mapper.fetch("tables", database="db01")
        # We can safely use | here, as both sets are independant
        self.assertEqual(mapper.map.items, x.items | y.items)

    async def test_using_unknown_depth_raises_exception(self):
        mapper = MetadataMapper(self.mapper.method)
        with self.assertRaisesRegex(
            ValueError,
            "Unknown map depth 'does_not_exist', allowed values: databases, tables, columns",
        ):
            await mapper.fetch("does_not_exist")

    async def test_metadatamapper_field_type_has_minimum_and_maximum(self):
        self.assertEqual(MetadataMapper.FIELD_TYPE.size.min, 1)
        self.assertEqual(MetadataMapper.FIELD_TYPE.size.max, 64)

    async def test_base_exception_happens(self):
        class CustomException(Exception):
            pass

        self.mapper._fetch = AsyncMock(side_effect=CustomException("Boom!"))

        with self.assertRaises(CustomException):
            await self.mapper.fetch("databases")

    async def test_cancelled(self):
        self.mapper._fetch = AsyncMock(side_effect=CancelledError("Boom!"))

        with self.assertRaises(CancelledError):
            await self.mapper.fetch("databases")
