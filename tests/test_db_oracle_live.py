import time
import unittest
import warnings
from typing import Any

import docker
import oracledb

from ending.ast import Identifier, IntType, Node
from ending.db import oracle
from ending.db.generic.method import Method
from ending.util.misc import to_bytes

try:
    from . import live_dbms
except ImportError:
    from tests import live_dbms


class LiveDBMS(live_dbms.LiveDBMS):
    SQL_CREATE_TABLE = """\
CREATE TABLE users (
    id INT,
    username VARCHAR(32),
    password VARCHAR(64),
    email VARCHAR(255),
    lastname VARCHAR(32),
    firstname VARCHAR(32),
    active BOOLEAN,
    avatar BLOB
)
"""
    SQL_INSERT = """INSERT INTO users VALUES(:1, :2, :3, :4, :5, :6, :7, :8)"""
    USER = "C##TEST"

    @classmethod
    def setup(cls) -> None:
        cls.client = docker.from_env()
        cls.container = cls.client.containers.run(
            "container-registry.oracle.com/database/free",
            environment={"ORACLE_PWD": "password"},
            detach=True,
            remove=True,
        )
        client = docker.APIClient(base_url="unix://var/run/docker.sock")
        details = client.inspect_container(cls.container.id)
        cls.container_ip = details["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        client.close()
        time.sleep(5)

        while True:
            time.sleep(1)
            try:
                cnx = oracledb.connect(
                    user="system", password="password", dsn=f"{cls.container_ip}:1521/"
                )
            except (oracledb.OperationalError, oracledb.DatabaseError) as e:
                continue
            else:
                cursor = cnx.cursor()
                cursor.execute(f"CREATE USER {cls.USER} IDENTIFIED BY password")
                cursor.execute(f"GRANT CREATE SESSION TO {cls.USER}")
                cursor.execute(f"GRANT CREATE TABLE TO {cls.USER}")
                cursor.execute(f"ALTER USER {cls.USER} QUOTA UNLIMITED ON users")
                # cnx.commit()
                cursor.close()
                cnx.close()
                break

    @classmethod
    def teardown(cls) -> None:
        cls.container.stop(timeout=2)
        cls.client.close()

    def connect(self) -> None:
        self.cnx = oracledb.connect(
            user=self.USER, password="password", dsn=f"{self.container_ip}:1521/"
        )
        self.cursor = self.cnx.cursor()

    def disconnect(self) -> None:
        self.cursor.close()
        self.cnx.close()

    def query(self, query: str) -> list[list[Any]]:
        try:
            self.cursor.execute(query)
        except oracledb.DatabaseError as e:
            raise live_dbms.SQLError(str(e))
        return self._cursor_to_results(self.cursor)

    def _convert_cell(self, cell: Any) -> Any:
        match cell:
            case oracledb.LOB():
                return cell.read()
            case _:
                return super()._convert_cell(cell)

    def execute(self, query: str) -> None:
        self.cursor.execute(query)
        self.cnx.commit()

    def executemany(self, query: str, rows: list) -> None:
        self.cursor.executemany(query, rows)
        self.cnx.commit()


# NOTE The mapper tests do not change the logic or expected results, they just convert
# the filters and results to UPPERCASE as it's Oracle's default behavior to return
# identifiers in uppercase if not quoted.
@unittest.skipIf(live_dbms.skip_docker_tests(), "docker is not available")
class TestLiveDBMS(live_dbms.TestLiveDBMS):
    DBMS = LiveDBMS
    dbms_module = oracle

    check_results = live_dbms.TestLiveDBMS.check_results_unsorted

    MAPPER_MAIN_SCHEMA = "C##TEST"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "USERS": {
                "ID": {"type": "NUMBER"},
                "USERNAME": {"type": "VARCHAR2"},
                "PASSWORD": {"type": "VARCHAR2"},
                "EMAIL": {"type": "VARCHAR2"},
                "LASTNAME": {"type": "VARCHAR2"},
                "FIRSTNAME": {"type": "VARCHAR2"},
                "ACTIVE": {"type": "BOOLEAN"},
                "AVATAR": {"type": "BLOB"},
            }
        }
    }

    async def test_mapper_filters(self) -> None:
        method = self._get_selectmethod()
        mapper = self.dbms_module.Mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, table="USER?", column="*NAME"
        )
        self.assertEqual(
            map.items,
            {
                self.MAPPER_MAIN_SCHEMA: {
                    "USERS": {"USERNAME": {}, "FIRSTNAME": {}, "LASTNAME": {}}
                }
            },
        )

    async def test_mapper_selectmethod_get_columns_with_wildcard_filters(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, table="USER?", column="*NAME"
        )
        self.assertEqual(
            map.items,
            {
                self.MAPPER_MAIN_SCHEMA: {
                    "USERS": {"USERNAME": {}, "FIRSTNAME": {}, "LASTNAME": {}}
                }
            },
        )

    async def test_mapper_selectmethod_get_tables_with_exact_column_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "tables", database=self.MAPPER_MAIN_SCHEMA, column="LASTNAME"
        )
        self.assertEqual(
            map.items,
            {self.MAPPER_MAIN_SCHEMA: {"USERS": {}}},
        )

    async def test_mapper_selectmethod_get_columns_with_exact_table_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, table="USERS"
        )
        self.assertEqual(
            map.items,
            {
                self.MAPPER_MAIN_SCHEMA: {
                    "USERS": {
                        "ACTIVE": {},
                        "AVATAR": {},
                        "EMAIL": {},
                        "FIRSTNAME": {},
                        "ID": {},
                        "LASTNAME": {},
                        "PASSWORD": {},
                        "USERNAME": {},
                    }
                }
            },
        )

    async def test_mapper_selectmethod_get_columns_with_exact_column_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, column="LASTNAME"
        )
        self.assertEqual(
            map.items,
            {self.MAPPER_MAIN_SCHEMA: {"USERS": {"LASTNAME": {}}}},
        )

    async def _selectmethod_inject(self, payload: Node) -> bytes:
        payload = f"' UNION ALL {payload} -- -'"
        query = f"SELECT username, password, email FROM users WHERE username LIKE '{payload}'"
        result = self.live_dbms.inject(query)
        # print(query)
        # print(result)
        return result

    def assertUntypedIsEquivalent(self, typed, untyped: str):
        match typed:
            case bool():
                self.assertEqual("true" if typed else "false", untyped.lower())
            case _:
                return super().assertUntypedIsEquivalent(
                    typed, untyped.encode() if isinstance(untyped, str) else untyped
                )
