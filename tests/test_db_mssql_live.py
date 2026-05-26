import unittest
from typing import NoReturn

from ending.ast import Identifier, IntType, Query
from ending.db import mssql
from ending.db.generic.method import Method

try:
    from . import live_dbms
except ImportError:
    from tests import live_dbms


class LiveDBMS(live_dbms.DockerizedODBCLiveDBMS):
    USERNAME = "sa"
    PASSWORD = "Password1"
    DOCKER_IMAGE = "mcr.microsoft.com/mssql/server"
    DOCKER_ENV = {
        "ACCEPT_EULA": "Y",
        "MSSQL_SA_PASSWORD": PASSWORD,
    }
    CONNECTION_STRING = (
        "DRIVER={{ODBC Driver 18 for SQL Server}};"
        "SERVER={ip};"
        "UID={username};"
        "PWD={password};"
        "TrustServerCertificate=yes"
    )

    # MsSQL does not have a BOOLEAN type: we'll use a BIT(1) instead.
    SQL_CREATE_TABLE = """\
CREATE TABLE users (
    id INT,
    username VARCHAR(32),
    password VARCHAR(64),
    email VARCHAR(255),
    lastname VARCHAR(32),
    firstname VARCHAR(32),
    active BIT,
    avatar VARBINARY(1024),
    CONSTRAINT users_id_unique UNIQUE (id)
);
CREATE INDEX users_id_index ON users (id);
"""
    SQL_INSERT = """INSERT INTO users VALUES(?, ?, ?, ?, ?, ?, ?, ?)"""

    def get_rows(self):
        rows = super().get_rows()
        for row in rows:
            row[6] = int(row[6]) if row[6] is not None else None
        return rows

    def get_query(self) -> Query:
        query = super().get_query()
        query = query.columns(
            *query.q.columns[:6],
            Identifier("active", type=IntType(min=0, max=1)),
            *query.q.columns[7:],
        )
        return query


@unittest.skipIf(live_dbms.skip_docker_tests(), "docker is not available")
class TestLiveDBMS(live_dbms.TestLiveDBMS):
    DBMS = LiveDBMS
    dbms_module = mssql

    check_results = live_dbms.TestLiveDBMS.check_results_unsorted

    MAPPER_MAIN_SCHEMA = "dbo"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "users": {
                "active": {"type": "bit"},
                "avatar": {"type": "varbinary"},
                "email": {"type": "varchar"},
                "firstname": {"type": "varchar"},
                "id": {"type": "int"},
                "lastname": {"type": "varchar"},
                "password": {"type": "varchar"},
                "username": {"type": "varchar"},
            }
        }
    }

    def test_select_boolean_true(self) -> NoReturn:
        self.skipTest("Boolean are not supported by MsSQL")

    def test_select_boolean_false(self) -> NoReturn:
        self.skipTest("Boolean are not supported by MsSQL")

    async def test_CastAsIntMethod(self) -> None:
        async def inject(payload) -> bytes:
            payload = f"SELECT * FROM users WHERE id=1 AND {payload}"
            return self.live_dbms.inject(payload)

        method = mssql.CastAsIntMethod(self.compiler, inject)
        return await self._test_generic_method(method)
