import unittest
from typing import Any

import docker
import psycopg2

from ending.ast import Identifier, Node
from ending.db import postgresql
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
    avatar BYTEA
);
"""

    @classmethod
    def setup(cls):
        cls.client = docker.from_env()
        # docker run --name some-postgres -e POSTGRES_PASSWORD=mysecretpassword -d postgres
        cls.container = cls.client.containers.run(
            "postgres",
            environment={"POSTGRES_PASSWORD": "toor"},
            detach=True,
            remove=True,
        )
        client = docker.APIClient(base_url="unix://var/run/docker.sock")
        details = client.inspect_container(cls.container.id)
        cls.container_ip = details["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        client.close()

        while True:
            try:
                cnx = psycopg2.connect(
                    host=cls.container_ip, user="postgres", password="toor"
                )
                cnx.set_session(autocommit=True)
                cursor = cnx.cursor()
                cursor.execute("CREATE DATABASE test;")
                cnx.commit()
                cursor.close()
                cnx.close()
            except psycopg2.OperationalError:
                continue
            else:
                break

    @classmethod
    def teardown(cls):
        cls.container.stop(timeout=2)
        cls.client.close()

    def connect(self):
        self.cnx = psycopg2.connect(
            host=self.container_ip, dbname="test", user="postgres", password="toor"
        )

    def disconnect(self):
        self.cnx.close()

    def query(self, query: str) -> list[list[Any]]:
        try:
            cursor = self.cnx.cursor()
            cursor.execute(query)
        except psycopg2.DatabaseError as e:
            cursor.close()
            # Reconnect because the current transaction is broken
            self.disconnect()
            self.connect()
            raise live_dbms.SQLError(str(e))
        return self._cursor_to_results(cursor)

    def execute(self, query: str):
        cursor = self.cnx.cursor()
        cursor.execute(query)
        self.cnx.commit()
        cursor.close()

    def executemany(self, query: str, rows: list):
        cursor = self.cnx.cursor()
        cursor.executemany(query, rows)
        self.cnx.commit()
        cursor.close()


@unittest.skipIf(live_dbms.skip_docker_tests(), "docker is not available")
class TestLiveDBMS(live_dbms.TestLiveDBMS):
    DBMS = LiveDBMS
    dbms_module = postgresql

    check_results = live_dbms.TestLiveDBMS.check_results_unsorted
    MAPPER_MAIN_SCHEMA = "public"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "users": {
                "active": {"type": "boolean"},
                "avatar": {"type": "bytea"},
                "email": {"type": "character varying"},
                "firstname": {"type": "character varying"},
                "id": {"type": "integer"},
                "lastname": {"type": "character varying"},
                "password": {"type": "character varying"},
                "username": {"type": "character varying"},
            }
        }
    }

    async def test_CastAsIntMethod(self):
        async def inject(payload):
            payload = f"SELECT * FROM users WHERE id=1 AND {payload}"
            return self.live_dbms.inject(payload)

        method = postgresql.CastAsIntMethod(self.compiler, inject)
        return await self._test_generic_method(method)

    def assertUntypedIsEquivalent(self, typed, untyped: str):
        match typed:
            case str():
                self.assertEqual(typed, untyped)
            case bool():
                self.assertEqual("true" if typed else "false", untyped)
            case int():
                self.assertEqual(str(typed), untyped)
            case bytes():
                typed = "\\x" + typed.hex()
                self.assertEqual(typed, untyped)
            case _:
                super().assertUntypedIsEquivalent(typed, untyped)
