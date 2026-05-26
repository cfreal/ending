import time
import unittest
import warnings
from typing import Any, NoReturn

import docker
from mysql import connector

from ending.db import mysql
from ending.util.misc import to_bytes

try:
    from . import live_dbms
except ImportError:
    from tests import live_dbms


class LiveDBMS(live_dbms.LiveDBMS):
    @classmethod
    def setup(cls):
        cls.client = docker.from_env()
        cls.container = cls.client.containers.run(
            "mariadb",
            environment={"MYSQL_ROOT_PASSWORD": "toor"},
            detach=True,
            remove=True,
        )
        client = docker.APIClient(base_url="unix://var/run/docker.sock")
        details = client.inspect_container(cls.container.id)
        cls.container_ip = details["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        client.close()

        warnings.filterwarnings("ignore", category=ResourceWarning)

        while True:
            time.sleep(0.5)
            try:
                cnx = connector.connect(
                    host=cls.container_ip, user="root", password="toor"
                )
            except:
                continue
            else:
                cursor = cnx.cursor()
                cursor.execute("CREATE DATABASE test;")
                cnx.commit()
                cursor.close()
                cnx.close()
                break

        warnings.filterwarnings("default", category=ResourceWarning)

    @classmethod
    def teardown(cls):
        cls.container.stop(timeout=2)
        cls.client.close()

    def connect(self):
        self.cnx = connector.connect(
            host=self.container_ip, user="root", password="toor", database="test"
        )
        self.cursor = self.cnx.cursor()

    def disconnect(self):
        self.cursor.close()
        self.cnx.close()

    def inject(self, query: str) -> bytes:
        try:
            self.cursor.execute(query)
        except connector.errors.DatabaseError as e:
            return to_bytes(str(e))
        return b"".join(to_bytes(item, b"NULL") for row in self.cursor for item in row)

    def query(self, query: str) -> list[list[Any]]:
        try:
            self.cursor.execute(query)
        except connector.errors.DatabaseError as e:
            raise live_dbms.SQLError(str(e))
        return self._cursor_to_results(self.cursor)

    def execute(self, query: str):
        self.cursor.execute(query)
        self.cnx.commit()

    def executemany(self, query: str, rows: list):
        self.cursor.executemany(query, rows)
        self.cnx.commit()


@unittest.skipIf(live_dbms.skip_docker_tests(), "docker is not available")
class TestLiveDBMS(live_dbms.TestLiveDBMS):
    DBMS = LiveDBMS
    dbms_module = mysql

    check_results = live_dbms.TestLiveDBMS.check_results_sorted

    MAPPER_MAIN_SCHEMA = "test"
    MAPPER_SCHEMA = {
        MAPPER_MAIN_SCHEMA: {
            "users": {
                "id": {"type": "int"},
                "username": {"type": "varchar"},
                "password": {"type": "varchar"},
                "email": {"type": "varchar"},
                "lastname": {"type": "varchar"},
                "firstname": {"type": "varchar"},
                "active": {"type": "tinyint"},
                "avatar": {"type": "blob"},
            }
        }
    }

    async def test_ExtractValue(self):
        async def inject(payload):
            payload = f"SELECT * FROM users WHERE id=1 AND {payload}"
            return self.live_dbms.inject(payload)

        method = mysql.ExtractValueMethod(self.compiler, inject)
        return await self._test_generic_method(method)
