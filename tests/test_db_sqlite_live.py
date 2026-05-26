import os
import sqlite3
import tempfile
from typing import Any

from ending.db import sqlite
from ending.util.misc import to_bytes

try:
    from . import live_dbms
except ImportError:
    from tests import live_dbms


class LiveDBMS(live_dbms.LiveDBMS):
    SQL_INSERT = """INSERT INTO users VALUES(?, ?, ?, ?, ?, ?, ?, ?)"""

    @classmethod
    def setup(cls):
        _, cls.db = tempfile.mkstemp()

    @classmethod
    def teardown(cls):
        os.unlink(cls.db)

    def connect(self):
        self.cnx = sqlite3.connect(self.db)
        self.cursor = self.cnx.cursor()

    def disconnect(self):
        self.cursor.close()
        self.cnx.close()

    def query(self, query: str) -> list[list[Any]]:
        try:
            self.cursor.execute(query)
        except sqlite3.DatabaseError as e:
            raise live_dbms.SQLError(str(e))
        return self._cursor_to_results(self.cursor)

    def execute(self, query: str):
        self.cursor.execute(query)
        self.cnx.commit()

    def executemany(self, query: str, rows: list):
        self.cursor.executemany(query, rows)
        self.cnx.commit()


class TestLiveDBMS(live_dbms.TestLiveDBMS):
    DBMS = LiveDBMS
    dbms_module = sqlite
    MAPPER_MAIN_SCHEMA = "root"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "users": {
                "id": {"type": "INT"},
                "username": {"type": "VARCHAR(32)"},
                "password": {"type": "VARCHAR(64)"},
                "email": {"type": "VARCHAR(255)"},
                "lastname": {"type": "VARCHAR(32)"},
                "firstname": {"type": "VARCHAR(32)"},
                "active": {"type": "BOOLEAN"},
                "avatar": {"type": "BLOB"},
            }
        }
    }

    async def test_mapper_selectmethod_get_databases_exact(self):
        with self.assertRaises(NotImplementedError) as cm:
            await super().test_mapper_selectmethod_get_databases_exact()
        self.assertEqual(
            str(cm.exception), "SQLite mapping cannot be filtered by database"
        )
