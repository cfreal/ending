"""Runs a benchmark for various setups."""

import hashlib
import itertools
import sys
import timeit
from abc import ABC, abstractmethod
from asyncio import run
from pathlib import Path

from ending.ast import *
from ending.db import mysql, sqlite
from ending.db.generic.method import Method
from ending.struct.resultset import ResultSet
from ending.util import logging, quoting
from ending.util.misc import to_bytes
from ending.util.quoting import hexadecimal
from tests.live_dbms import LiveDBMS
from tests.test_db_mysql_live import LiveDBMS as MySQLLiveDBMS
from tests.test_db_sqlite_live import LiveDBMS as SQLiteLiveDBMS

logging.set_level("CRITICAL")


def avg(tries) -> float:
    return sum(tries) / len(tries)


def display_times(tries) -> None:
    best, worse, average = min(tries), max(tries), avg(tries)
    print(f"avg:  {average:.3g} seconds per loop")
    print(f"best:  {best:.3g} seconds per loop")
    print(f"worse: {worse:.3g} seconds per loop")
    print("all", tries)


class Runner(ABC):
    """A runner is an object that can be setup, run and teared down.
    It is used in conjunction with timeit to benchmark operations.
    """

    @abstractmethod
    async def setup(self) -> None:
        pass

    @abstractmethod
    async def teardown(self) -> None:
        pass

    @abstractmethod
    async def run(self) -> None:
        pass


class RawRunner(Runner):
    async def setup(self) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def run(self) -> None:
        async def inject(payload: Node) -> bool:
            payload = Union(Expr("a"), payload)
            compiler.wrap(payload)
            if "IS NULL" in str(payload):
                return False
            method._inject.i += 1
            return bool(method._inject.i % 2)

        compiler = mysql.Compiler(quote=hexadecimal)
        method = mysql.TestMethod(compiler, inject)

        async def returns_100(*args, **kwargs) -> int:
            return 100

        method.fetch_text_length = returns_100
        method.fetch_count = returns_100

        query = Query("table").columns(
            Identifier("id", type=IntType(min=0, max=100)),
            Identifier("user", type=TextType()),
            Identifier("password", type=TextType()),
        )

        method._inject.i = 0
        await method.fetch(query)


class DBMSRunner(Runner):
    dbms: LiveDBMS
    QUERY: str = "SELECT * FROM users WHERE username IS NOT NULL AND {payload} LIMIT 1"
    nb_injections: int
    results: ResultSet

    async def run(self) -> None:
        query = self.get_query()
        method = self.get_method()
        self.nb_injections = 0
        self.results = await method.fetch(query)

    def get_query(self) -> Node:
        return (
            Query("users")
            .columns(
                Identifier("username", type=TextType()),
                Identifier("password", type=TextType()),
                # Identifier("email", type=TextType()),
                Identifier("avatar", type=BlobType()),
            )
            .limit(50)
        )

    @abstractmethod
    def get_method(self) -> Method:
        pass

    async def inject(self, payload) -> bool:
        payload = self.QUERY.format(payload=payload)
        result = self.dbms.inject(payload)
        self.nb_injections += 1
        return result != b""

    async def setup(self) -> None:
        self.dbms = self.LiveDBMS()
        self.dbms.setup()
        self.dbms.create_dummy_data()
        self.dbms.connect()

    async def teardown(self) -> None:
        self.dbms.disconnect()
        self.dbms.teardown()


class SQLiteRunner(DBMSRunner):
    LiveDBMS = SQLiteLiveDBMS

    def get_method(self) -> Method:
        compiler = sqlite.Compiler(quote=quoting.singlequote)
        return sqlite.TestMethod(compiler, self.inject)


class MySQLRunner(DBMSRunner):
    LiveDBMS = MySQLLiveDBMS

    def get_method(self) -> Method:
        compiler = mysql.Compiler(quote=quoting.singlequote)
        return mysql.TestMethod(compiler, self.inject)


class FakeRunner(DBMSRunner):
    LiveDBMS = SQLiteLiveDBMS

    async def inject(self, payload) -> bool:
        self.nb_injections += 1
        payload = str(payload)
        if ("'41'" in payload or ",65," in payload) and "NOT IN" not in payload:
            return True
        return False

    def get_method(self) -> Method:
        compiler = sqlite.Compiler(quote=quoting.singlequote)
        return sqlite.TestMethod(compiler, self.inject)

    async def teardown(self) -> None:
        pass


# --- RUN ---------


def do_run() -> None:
    run(runner.run())
    sys.stderr.write(".")
    sys.stderr.flush()


if len(sys.argv) < 3:
    print("Usage: python -m tests.benchmark <repeat> [runner]")
    print("runner: sqlite, mysql, fake")
    sys.exit(1)

match sys.argv[2]:
    case "sqlite":
        runner = SQLiteRunner()
    case "mysql":
        runner = MySQLRunner()
    case "fake":
        runner = FakeRunner()
    case _:
        raise ValueError(f"Unknown runner: {sys.argv[2]}")

run(runner.setup())
times = None

try:
    repeat = int(sys.argv[1])

    timer = timeit.Timer(do_run)
    times = timer.repeat(repeat, 1)

    print("")
    print("injections:", runner.nb_injections)
    print("hash:", hashlib.md5(to_bytes(str(runner.results))).hexdigest())
finally:
    sys.stderr.write("\n")
    sys.stderr.flush()
    run(runner.teardown())

if times:
    display_times(times)

# ----------- PROFILE ----------------
# python -m cProfile -s cumulative -m tests.benchmark | tee /dev/shm/calls.txt
# go()
