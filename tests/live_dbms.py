import hashlib
import os
import pathlib
import time
import sys
from abc import ABC, abstractmethod
from typing import Any, Type
from unittest import IsolatedAsyncioTestCase

import docker
import pyodbc

from ending.ast import *
from ending.db.generic.compiler import Compiler
from ending.db.generic.map import Mapper
from ending.db.generic.method import HexSelectMethod, Method, SelectMethod
from ending.struct.resultset import ResultSet
from ending.util import logging, quoting
from ending.util.misc import to_bytes

__all__ = [
    "LiveDBMS",
    "DockerizedODBCLiveDBMS",
    "TestLiveDBMS",
    "skip_docker_tests",
    "SQLError",
]


def skip_docker_tests() -> bool:
    if not os.getenv("RUN_DOCKER_TESTS"):
        return True
    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        return True
    client.close()
    return False


class SQLError(Exception):
    """Raised when some query raises an SQL error."""


class LiveDBMS(ABC):
    """A class than can be used to create a live database server for testing, and run
    queries against it.
    """

    rows: list[list[Any]]
    """Data stored in the database."""
    base_query: Query
    """SQL query to use to fetch the data."""

    TABLE_NAME = "users"
    """Dummy table."""
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
);
"""
    """Query to create the dummy table."""
    SQL_INSERT = """INSERT INTO users VALUES(%s, %s, %s, %s, %s, %s, %s, %s)"""
    """Query to insert a row in the dummy table."""

    @classmethod
    def setup(cls):
        """Creates the database server."""

    def create_dummy_data(self) -> None:
        """Creates the `users` table and fills it with dummy data."""
        self.connect()
        self.rows = self.get_rows()
        self.base_query = self.get_query()
        self.execute(self.SQL_CREATE_TABLE)
        self.executemany(self.SQL_INSERT, self.rows)
        self.disconnect()

    def get_rows(self) -> list[list[Any]]:
        db_file = pathlib.Path(__file__).parent / "fixtures" / "db.csv"
        rows = []

        # Add a NULL row to check how it is handled by injection methods
        rows.append([None] * 8)

        for id, row in enumerate(db_file.read_text().split("\n")):
            lastname, firstname, email, username, password = row.split(",")
            id += 1
            active = id % 3 == 0
            # Generate fake data for the avatar blob.
            image = hashlib.sha256(row.encode()).digest()
            rows.append(
                [id, username, password, email, lastname, firstname, active, image]
            )

        return rows

    def get_query(self) -> Query:
        text_type = TextType(charset=TextType.charset + "çÉêïèîëô é")
        return Query("users").columns(
            Identifier("id", type=IntType()),
            Identifier("username", type=text_type),
            Identifier("password", type=text_type),
            Identifier("email", type=text_type),
            Identifier("lastname", type=text_type),
            Identifier("firstname", type=text_type),
            Identifier("active", type=BoolType()),
            Identifier("avatar", type=BlobType()),
        )

    @classmethod
    def teardown(cls) -> None:
        """Stops and removes the database server."""

    @abstractmethod
    def connect(self) -> None:
        """Connects to the database."""

    def disconnect(self) -> None:
        """Disconnects from the database."""

    def inject(self, query: str) -> bytes:
        """Runs a query and returns bytes that contains the results or the error."""
        try:
            results = self.query(query)
        except SQLError as e:
            # print(e, str(e))
            return b"ERROR:" + to_bytes(str(e))

        return b"".join(
            to_bytes(item, "n") for row in results for item in row if item is not None
        )

    @abstractmethod
    def execute(self, query: str) -> None:
        """Executes a command on the database."""

    @abstractmethod
    def executemany(self, query: str, data: list) -> None:
        """Executes a command on the database, multiple times."""

    @abstractmethod
    def query(self, query: str) -> list[list[Any]]:
        """Runs a query and returns the results as an array. If an error occurs, raises
        an `SQLError`.
        """

    def _cursor_to_results(self, cursor) -> list[list[Any]]:
        return [[self._convert_cell(cell) for cell in row] for row in cursor]

    def _convert_cell(self, cell: Any) -> Any:
        match cell:
            case memoryview():
                return cell.tobytes()
            case _:
                return cell


class DockerizedODBCLiveDBMS(LiveDBMS):
    """A LiveDBMS that spawns a docker container and connects to it through
    PyODBC.
    """

    USERNAME = "root"
    """Database username."""
    PASSWORD = "toor"
    """Database password."""
    DOCKER_IMAGE = "some/container"
    """Name of the docker image of the database."""
    DOCKER_ENV = {}
    """Environment variables to use when creating the docker."""
    CONNECTION_STRING = (
        "DRIVER={{...}};" "SERVER={ip};" "UID={username};" "PWD={password};" "...;"
    )
    """PyODBC connection string. `ip`, `username`, and `password` will be
    replaced by their proper value.
    """

    @classmethod
    def setup(cls):
        cls.client = docker.from_env()
        cls.container = cls.client.containers.run(
            cls.DOCKER_IMAGE,
            environment=cls.DOCKER_ENV,
            detach=True,
            remove=True,
        )

        client = docker.APIClient(base_url="unix://var/run/docker.sock")
        details = client.inspect_container(cls.container.id)
        cls.container_ip = details["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        client.close()

        while True:
            time.sleep(0.5)
            try:
                cnx = pyodbc.connect(
                    cls._get_connection_string(with_db=False), autocommit=True
                )
            except pyodbc.InterfaceError:
                continue
            else:
                cursor = cnx.cursor()
                cursor.execute("CREATE DATABASE test;")
                cnx.commit()
                cursor.close()
                cnx.close()
                break

    @classmethod
    def _get_connection_string(cls, with_db=True):
        string = cls.CONNECTION_STRING.format(
            ip=cls.container_ip,
            username=cls.USERNAME,
            password=cls.PASSWORD,
        )
        if not with_db:
            return string
        return string + ";DATABASE=test;"

    @classmethod
    def teardown(cls):
        cls.container.stop(timeout=2)
        cls.client.close()

    def connect(self):
        self.cnx = pyodbc.connect(self._get_connection_string())
        self.cursor = self.cnx.cursor()

    def disconnect(self):
        self.cursor.close()
        self.cnx.close()

    def execute(self, query: str) -> None:
        self.cursor.execute(query)
        self.cnx.commit()

    def executemany(self, query: str, rows: list) -> None:
        self.cursor.executemany(query, rows)
        self.cnx.commit()

    def query(self, query: str) -> list[list[Any]]:
        try:
            self.cursor.execute(query)
        except (pyodbc.Error, SystemError) as e:
            raise SQLError(str(e))
        return self._cursor_to_results(self.cursor)


class TestLiveDBMS(IsolatedAsyncioTestCase):
    MAPPER_MAIN_SCHEMA = "test"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "users": {
                "id": {},
                "username": {},
                "password": {},
                "email": {},
                "lastname": {},
                "firstname": {},
                "active": {},
                "avatar": {},
            }
        }
    }

    DBMS: Type[LiveDBMS]
    DEBUG: bool = False
    PAUSE: bool = False
    dbms_module = ...

    compiler: Compiler

    def get_row_by_id(self, id: int, *columns: str) -> list:
        row = self.drows[id]
        if not columns:
            return row
        positions = [
            next(i for i, c in enumerate(self.query.q.columns) if c.name == name)
            for name in columns
        ]
        return [row[i] for i in positions]

    @classmethod
    def setUpClass(cls):
        cls.DBMS.setup()

        db = cls.DBMS()
        db.create_dummy_data()
        cls.drows = {row[0]: row for row in db.rows}
        cls.query = db.base_query
        cls.rows = db.rows

        if cls.DEBUG:
            logging.set_level("SQL")

        if cls.PAUSE:
            print("[PAUSED] Press key to continue...")
            sys.stdin.read(1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.DBMS.teardown()
        if cls.DEBUG:
            logging.set_level("DEBUG")

    def setUp(self) -> None:
        self.live_dbms = self.DBMS()
        self.live_dbms.connect()
        self.compiler = self.dbms_module.Compiler(quote=quoting.singlequote)

    def tearDown(self) -> None:
        self.live_dbms.disconnect()

    # Methods to verify consistency of results

    def check_results_unsorted(self, results: ResultSet, nb_rows: int = None):
        """Verifies that the results that were obtained have the right number of
        rows and are all in the expected result set.
        """
        if nb_rows is not None:
            self.assertEqual(nb_rows, len(results.data))

        # Verify that we did not dump the same result twice
        ids = [row[0] for row in results.data]
        self.assertEqual(len(set(ids)), len(ids))

        # Verify that each result row is from the result set
        for row in results.data:
            if row not in self.rows:
                if row and isinstance(row[0], int):
                    try:
                        expected = self.drows[row[0]]
                    except KeyError:
                        pass
                    else:
                        self.fail(
                            f"row {row!r} is not in SQL results, expected {expected!r}"
                        )
                self.fail(f"row {row!r} is not in SQL results")

    def check_results_sorted(self, results: ResultSet, nb_rows: int = None):
        """Verifies that the results that were obtained have the right number of
        rows and are all in the expected result set, in order.
        """
        if nb_rows is not None:
            self.assertEqual(nb_rows, len(results.data))
            rows = self.rows[:nb_rows]
        else:
            rows = self.rows
        self.assertEqual(rows, results.data)

    def check_single_result_is(self, results: ResultSet, value: Any):
        """Verifies that the result set is one cell that has given value."""
        self.assertEqual(1, len(results.data))
        self.assertEqual(1, len(results.data[0]))
        self.assertEqual(value, results.data[0][0])

    check_results = check_results_unsorted
    """Indicates how results should be checked."""

    # UnknownType casting verifications

    async def test_fetching_unknowntype_produces_proper_values_for_SelectMethod(self):
        method = self._get_selectmethod()
        await self.check_fetching_unknowntype_produces_proper_values(method, 100)

    async def test_fetching_unknowntype_produces_proper_values_for_SelectMethod_with_hex(
        self,
    ):
        if not issubclass(self.dbms_module.SelectMethod, HexSelectMethod):
            self.skipTest("HexSelectMethod is not implemented for this DBMS")
        method = self._get_selectmethod(hex=True)
        await self.check_fetching_unknowntype_produces_proper_values(method, 100)

    async def test_fetching_unknowntype_produces_proper_values_for_TestMethod(self):
        method = self._get_testmethod()
        await self.check_fetching_unknowntype_produces_proper_values(method, 2)

    async def check_fetching_unknowntype_produces_proper_values(
        self, method: Method, rows: int
    ):
        columns = [
            Identifier("id", type=IntType()),
            "username",
            "active",
            "avatar",
        ]
        query = self.query.columns(*columns).limit(rows)
        results = await method.fetch(query)

        for row in results.data:
            id, *row = row
            base = self.get_row_by_id(id, "username", "active", "avatar")
            for typed, untyped in zip(base, row):
                self.assertUntypedIsEquivalent(typed, untyped)

    def assertUntypedIsEquivalent(self, typed, untyped: bytes) -> None:
        match typed:
            case None:
                self.assertEqual(typed, untyped)
            case bool():
                self.assertEqual(b"1" if typed else b"0", untyped)
            case int():
                self.assertEqual(str(typed).encode(), untyped)
            case str():
                self.assertEqual(typed.encode(), untyped)
            case bytes():
                self.assertEqual(typed, untyped)
            case _:
                raise TypeError(f"Unknown type: {(type(typed))}")

    # TESTS FOR COMPILER

    def _test_query(self, query: Query):
        self.compiler.wrap(query)
        query = str(query)

        try:
            results = self.live_dbms.query(query)
        except SQLError as e:
            self.fail(f"{query} -> {e}")

        return results

    def _test_select_single_value(self, value: Any):
        query = Query(None).columns(Value(value))
        results = self._test_query(query)
        self.assertEqual(results, [[value]])

    def test_select_integer(self):
        self._test_select_single_value(123)

    def test_select_string(self):
        self._test_select_single_value("test string! 123")

    def test_select_null(self):
        self._test_select_single_value(None)

    def test_select_boolean_true(self):
        self._test_select_single_value(True)

    def test_select_boolean_false(self):
        self._test_select_single_value(False)

    def test_query_identifiers(self):
        query = self.query
        results = self._test_query(query)
        results = ResultSet(query, data=results)
        return self.check_results_unsorted(results)

    def test_query_limit(self):
        # TODO Check that we indeed return the proper rows instead of just counting
        for start in (0, 3):
            for count in (1, 10):
                with self.subTest(start=start, count=count):
                    query = self.query.limit(start, count)
                    results = self._test_query(query)
                    results = ResultSet(query, data=results)
                    self.check_results_unsorted(results, nb_rows=count)

    def test_query_order_reversed(self):
        query = self.query.order(Identifier("id"), reverse=True)
        results = self._test_query(query)
        results = ResultSet(query, data=results)
        self.check_results_unsorted(results)

        # Check ordering by comparing the id columns

        previous_id = 100000

        for row in results.data:
            id = row[0]
            if id is None:
                continue
            if id > previous_id:
                self.fail(f"row {row} is not in order (previous: {previous_id})")
            previous_id = id

    def test_query_order(self):
        query = self.query.order(Identifier("id"))
        results = self._test_query(query)
        results = ResultSet(query, data=results)
        self.check_results_unsorted(results)

        # Check ordering by comparing the id columns

        previous_id = 0

        for row in results.data:
            id = row[0]
            if id is None:
                continue
            if id < previous_id:
                self.fail(f"row {row} is not in order (previous: {previous_id})")
            previous_id = id

    # TESTS FOR METHODS

    # SelectMethod

    async def _selectmethod_inject(self, payload: Node) -> bytes:
        payload = f"' UNION ALL {payload} -- -"
        query = f"SELECT username, password, email FROM users WHERE username LIKE '{payload}'"
        result = self.live_dbms.inject(query)
        return result

    def _get_selectmethod(self, **kwargs) -> SelectMethod:
        if not hasattr(self.dbms_module, "SelectMethod"):
            self.skipTest("SelectMethod is not implemented for this DBMS")
        kwargs = (
            dict(
                compiler=self.compiler,
                inject=self._selectmethod_inject,
                columns=3,
                column=1,
                nb_rows=20,
            )
            | kwargs
        )
        return self.dbms_module.SelectMethod(**kwargs)

    async def test_SelectMethod(self):
        NB_ROWS = 100

        query = self.query.limit(NB_ROWS)
        method = self._get_selectmethod()
        results = await method.fetch(query)
        self.check_results(results, NB_ROWS)

    async def test_SelectMethod_with_payload_echoed(self):
        NB_ROWS = 100

        async def inject(payload: Node) -> bytes:
            return str(payload).encode() + await self._selectmethod_inject(payload)

        query = self.query.limit(NB_ROWS)
        method = self._get_selectmethod(inject=inject)
        results = await method.fetch(query)
        self.check_results(results, NB_ROWS)

    async def test_SelectMethod_with_payload_echoed_with_hex(self):
        if not issubclass(self.dbms_module.SelectMethod, HexSelectMethod):
            self.skipTest("HexSelectMethod is not implemented for this DBMS")

        NB_ROWS = 100

        async def inject(payload: Node) -> bytes:
            return str(payload).encode() + await self._selectmethod_inject(payload)

        query = self.query.limit(NB_ROWS)
        method = self._get_selectmethod(inject=inject, hex=True)
        results = await method.fetch(query)
        self.check_results(results, NB_ROWS)

    async def test_SelectMethod_with_hex(self):
        if not issubclass(self.dbms_module.SelectMethod, HexSelectMethod):
            self.skipTest("HexSelectMethod is not implemented for this DBMS")

        NB_ROWS = 100

        query = self.query.limit(NB_ROWS)
        method = self._get_selectmethod(hex=True)
        results = await method.fetch(query)
        self.check_results(results, NB_ROWS)

    # TestMethod

    async def _testmethod_inject(self, payload: Node) -> bool:
        payload = f"1' AND {payload:p} AND 'a'='a"
        query = f"SELECT * FROM users WHERE id='{payload}'"
        data = self.live_dbms.inject(query)
        result = b"Vadeboncoeur" in data
        # print(payload, result)
        return result

    def _get_testmethod(self, **kwargs):
        if not hasattr(self.dbms_module, "TestMethod"):
            self.skipTest("TestMethod is not implemented for this DBMS")
        kwargs = dict(compiler=self.compiler, inject=self._testmethod_inject) | kwargs
        return self.dbms_module.TestMethod(**kwargs)

    async def test_TestMethod(self):
        NB_ROWS = 3
        query = self.query.limit(NB_ROWS)
        method = self._get_testmethod()
        results = await method.fetch(query)
        self.check_results(results, NB_ROWS)

    async def test_TestMethod_fetch_text_with_multibyte_char(self):
        # lastname is "Dubé"
        lastname = Identifier("lastname", type=TextType(TextType.charset + "éèà"))
        query = Query("users").columns(lastname).where(Identifier("id") == 2)

        method = self._get_testmethod()
        results = await method.fetch(query)
        self.check_single_result_is(results, "Dubé")

    async def test_TestMethod_fetch_text_with_multibyte_char_with_wildcard(self):
        # lastname is "Dubé"
        lastname = Identifier("lastname", type=TextType())
        query = Query("users").columns(lastname).where(Identifier("id") == 2)
        method = self._get_testmethod(wildcard="?")
        results = await method.fetch(query)
        self.check_single_result_is(results, "Dub?")

    async def test_TestMethod_fetch_blob_with_wildcard(self):
        # lastname is "Dubé"
        avatar = Identifier("avatar", type=BlobType(byteset=bytes(range(0, 100))))
        query = Query("users").columns(avatar).where(Identifier("id") == 2)
        method = self._get_testmethod(wildcard="?")
        results = await method.fetch(query)
        self.check_single_result_is(
            results, b"?\x17\x1f??\x06??K?\x1f???????F?E????0????_?"
        )

    # Other methods

    async def _test_generic_method(self, method: Method, nb_rows: int = 10):
        query = self.query.limit(nb_rows)
        results = await method.fetch(query)
        self.check_results(results, nb_rows)

    # Mapper

    def __mapper_expected_databases(self):
        return {d: {} for d in self.MAPPER_SCHEMA}

    def __mapper_expected_tables(self):
        return {d: {t: {} for t in ts} for d, ts in self.MAPPER_SCHEMA.items()}

    def __mapper_expected_columns(self):
        return {
            d: {t: {c: {} for c in cs} for t, cs in ts.items()}
            for d, ts in self.MAPPER_SCHEMA.items()
        }

    def __mapper_expected_types(self):
        return self.MAPPER_SCHEMA

    def _get_mapper(self, method: Method) -> Mapper:
        if not hasattr(self.dbms_module, "Mapper"):
            self.skipTest("Mapper is not implemented for this DBMS")
        return self.dbms_module.Mapper(method)

    async def test_mapper_selectmethod_databases(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("databases", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(map.items, self.__mapper_expected_databases())

    async def test_mapper_selectmethod_tables(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("tables", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(map.items, self.__mapper_expected_tables())

    async def test_mapper_selectmethod_columns(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("columns", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(
            map.items,
            self.__mapper_expected_columns(),
        )

    async def test_mapper_testmethod_databases(self):
        method = self._get_testmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("databases", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(map.items, self.__mapper_expected_databases())

    async def test_mapper_testmethod_tables(self):
        method = self._get_testmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("tables", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(map.items, self.__mapper_expected_tables())

    async def test_mapper_testmethod_columns(self):
        method = self._get_testmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("columns", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(
            map.items,
            self.__mapper_expected_columns(),
        )

    async def test_mapper_selectmethod_get_databases_exact(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        db_filter = "?" + self.MAPPER_MAIN_SCHEMA[1:]
        map = await mapper.fetch("databases", database=db_filter)
        self.assertEqual(
            map.items,
            {self.MAPPER_MAIN_SCHEMA: {}},
        )

    async def test_mapper_selectmethod_get_columns_with_wildcard_filters(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, table="user?", column="*name"
        )
        self.assertEqual(
            map.items,
            {
                self.MAPPER_MAIN_SCHEMA: {
                    "users": {"username": {}, "firstname": {}, "lastname": {}}
                }
            },
        )

    async def test_mapper_selectmethod_get_columns_with_exact_column_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, column="lastname"
        )
        self.assertEqual(
            map.items,
            {self.MAPPER_MAIN_SCHEMA: {"users": {"lastname": {}}}},
        )

    async def test_mapper_selectmethod_get_columns_with_exact_table_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, table="users"
        )
        self.assertEqual(
            map.items,
            {
                self.MAPPER_MAIN_SCHEMA: {
                    "users": {
                        "active": {},
                        "avatar": {},
                        "email": {},
                        "firstname": {},
                        "id": {},
                        "lastname": {},
                        "password": {},
                        "username": {},
                    }
                }
            },
        )

    async def test_mapper_selectmethod_get_tables_with_exact_column_filter(self):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "tables", database=self.MAPPER_MAIN_SCHEMA, column="lastname"
        )
        self.assertEqual(
            map.items,
            {self.MAPPER_MAIN_SCHEMA: {"users": {}}},
        )

    async def test_mapper_selectmethod_get_tables_with_exact_but_incorrect_column_filter(
        self,
    ):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "tables", database=self.MAPPER_MAIN_SCHEMA, column="lastnamexxx"
        )
        self.assertEqual(
            map.items,
            {},
        )

    async def test_mapper_selectmethod_get_columns_with_exact_but_incorrect_column_filter(
        self,
    ):
        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch(
            "columns", database=self.MAPPER_MAIN_SCHEMA, column="lastnamexxx"
        )
        self.assertEqual(
            map.items,
            {},
        )

    @abstractmethod
    async def test_mapper_types(self):
        if not hasattr(self.dbms_module, "Mapper"):
            self.skipTest("Mapper is not implemented for this DBMS")

        method = self._get_selectmethod()
        mapper = self._get_mapper(method)

        map = await mapper.fetch("types", database=self.MAPPER_MAIN_SCHEMA)
        self.assertEqual(map.items, self.__mapper_expected_types())
