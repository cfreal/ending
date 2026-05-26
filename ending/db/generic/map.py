"""Database schema and objects able to retrieve this schema.

Examples:

    Fetch the whole schema (databases, tables, columns):

    >>> await mapper.fetch("columns")
    Map
    ├── information_schema
        └── ...
    ├── test
        ├── users
        │   ├── lastname
        │   ├── firstname
        │   ├── email
        │   ├── username
        │   └── password
        └── administrators
            ├── lastname
            ├── firstname
            ├── email
            ├── username
            └── password
    ├── sys
        └── ...
    ├── performance_schema
        └── ...
    └── mysql
        └── ...

    Fetch tables whose name begins by `user`:

    >>> await mapper.fetch("tables", table="user*")
    Map
    ├── information_schema
    │   ├── USER_PRIVILEGES
    │   ├── USER_STATISTICS
    │   └── user_variables
    ├── test
    │   └── users
    ├── sys
    │   ├── user_summary
    │   ├── user_summary_by_statement_type
    │   ├── user_summary_by_file_io_type
    │   ├── user_summary_by_stages
    │   ├── user_summary_by_statement_latency
    │   └── user_summary_by_file_io
    ├── performance_schema
    │   ├── user_variables_by_thread
    │   └── users
    └── mysql
        └── user

"""

from __future__ import annotations

import asyncio
import csv
import functools
import operator
import re
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, overload

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text
from rich.tree import Tree

from ending.ast import (
    Comparison,
    Count,
    Identifier,
    IntType,
    Node,
    Query,
    TextType,
    WordedComparison,
)
from ending.db.generic.method import Method
from ending.struct.metadata import (
    Listener,
    Context,
    Listener,
    MapState,
    QueryState,
)
from ending.struct.resultset import PartialCell, ResultSet
from ending.struct.storable import Storable
from ending.util import logging
from ending.util.humanized import rich_cell

__all__ = [
    "Map",
    "Mapper",
    "MetadataMapper",
    "MapDepth",
    "MapQueryListener",
]

MapDepth = Literal["databases", "tables", "columns", "types"]


@dataclass
class Map(Storable):
    """A map of the schema of a target.

    Contains a list of databases, which each contain a list of tables, which
    each contain a list of columns, as nested dictionaries.

    The column dictionary may contain metadata, such as the type of columns.
    """

    items: dict[str, dict[str, dict[str, dict[str, Any]]]] = field(default_factory=dict)

    def add(
        self, db: str, table: str = None, column: str = None, type: str = None
    ) -> None:
        """Adds a database, table, column to the map."""
        tables = self.items.setdefault(db, {})
        if not table:
            return
        columns = tables.setdefault(table, {})
        if not column:
            return
        metadata = columns.setdefault(column, {})
        if not type:
            return
        metadata["type"] = type

    def tree(self) -> Tree:
        """Creates a tree from the map."""
        tree = Tree("Map", hide_root=True)

        prefixes = {
            "db": Text("◼", style="blue"),
            "table": Text("◼", style="red"),
            "column": Text("◼", style="green"),
        }

        def add(tree: Tree, type: str, *values) -> Tree:
            columns = Columns((prefixes[type],) + values)
            return tree.add(Align(columns, align="left", pad=False))

        for db, tables in self.items.items():
            db = add(tree, "db", rich_cell(db))
            for table, columns in tables.items():
                table = add(db, "table", rich_cell(table))
                for column, metadata in columns.items():
                    if "type" in metadata:
                        add(
                            table,
                            "column",
                            rich_cell(column),
                            Text(str(metadata["type"]), style="blue italic"),
                        )
                    else:
                        add(table, "column", rich_cell(column))
        return tree

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield self.tree()

    def store_as_rich(self, path: str) -> None:
        """Stores the object as it was displayed in the terminal."""
        from ending.cli.misc import build_console

        with open(path, "w") as handle:
            console = build_console(width=1024**2, file=handle)
            console.print(self.tree())

    def __str__(self) -> str:
        tree = Tree("Map", hide_root=True)

        def add(tree: Tree, value: str | PartialCell) -> Tree:
            return tree.add(Align(Text(f"{value}"), align="left", pad=False))

        for db, tables in self.items.items():
            db = add(tree, db)
            for table, columns in tables.items():
                table = add(db, table)
                for column, metadata in columns.items():
                    if "type" in metadata:
                        add(table, f"{column} [{metadata['type']}]")
                    else:
                        add(table, column)

        console = Console(
            width=1024**2, force_terminal=False, emoji=False, highlight=False
        )
        with console.capture() as capture:
            console.print(tree)
        return capture.get()

    def store_as_csv(self, path: str) -> None:
        """Saves the map as a CSV file."""
        with open(path, "w") as handle:
            writer = csv.writer(handle, escapechar="\\")
            writer.writerow(("db", "table", "column", "type"))
            for db, tables in self.items.items():
                if not tables:
                    writer.writerow((db,))
                    continue
                for table, columns in tables.items():
                    if not columns:
                        writer.writerow((db, table))
                        continue
                    for column, metadata in columns.items():
                        if not metadata:
                            writer.writerow((db, table, column))
                        else:
                            writer.writerow((db, table, column, metadata["type"]))

    @staticmethod
    def load(path: str) -> Map:
        """Creates a map from given CSV file."""
        map = Map()
        with open(path, "r") as handle:
            reader = csv.reader(handle)
            # Skip header
            iterator = iter(reader)
            next(iterator)
            for row in iterator:
                map.add(*row)
        return map

    def update(self, other: Map) -> None:
        """Merges the information from the other map with self."""
        for db, tables in other.items.items():
            self.add(db)
            for table, columns in tables.items():
                self.add(db, table)
                for column in columns.keys():
                    self.add(db, table, column)

    @staticmethod
    def _filter_match(filter: str, value: str) -> bool:
        """Applies filter to value and returns where it matches."""
        if filter is None:
            return True
        filter = re.escape(filter)
        filter = filter.replace("\\?", ".").replace("\\*", ".*")
        filter = f"^{filter}$"
        return re.match(filter, value, flags=re.IGNORECASE) is not None

    def filter(
        self,
        depth: MapDepth,
        *,
        database: str = None,
        table: str = None,
        column: str = None,
    ) -> Map:
        """Returns a new map with only the items that match the given filters."""

        def filter_by(items: dict[str, dict], *filters: list[str]) -> dict[str, dict]:
            if not filters:
                return items

            current_filter, *other_filters = filters
            items = {
                key: filter_by(sub_items, *other_filters)
                for key, sub_items in items.items()
                if self._filter_match(current_filter, key)
            }
            items = {
                key: sub_items
                for key, sub_items in items.items()
                if sub_items or not any(other_filters)
            }
            return items

        def keep_depth(items: dict[str, dict], depth: int) -> dict[str, dict]:
            if depth < 0:
                return {} if isinstance(items, dict) else items
            return {
                key: keep_depth(sub_items, depth - 1)
                for key, sub_items in items.items()
            }

        items = filter_by(self.items, database, table, column)

        try:
            ndepth = MapDepth.__args__.index(depth)
        except ValueError:
            raise TypeError(f"Unknown depth: {depth}")

        items = keep_depth(items, ndepth)

        return Map(items)

    @overload
    def radd(self, databases: list[str]) -> None: ...

    @overload
    def radd(self, database: str, tables: list[str]) -> None: ...

    @overload
    def radd(
        self, database: str, table: str, columns: list[str] | list[list[str, str]]
    ) -> None: ...

    def radd(self, *args) -> None:
        """Adds databases, tables, or columns to the map recursively."""
        iterable = args[-1]
        args = args[:-1]
        for row in iterable:
            if isinstance(row, str):
                self.add(*args, row)
            else:
                self.add(*args, *row)


class Mapper(ABC):
    """Builds and runs queries meant to dump the structure of the database (db, table,
    column, ...). Saves the structure in a `Map` object.

    Events:

    The mapper emits events to indicate the progress of the mapping process.

    - `dump:start`: Indicates the beginning of the enumeration (dumping) process for a
        specific depth (databases, tables, columns, or types).
    - `dump:count`: Communicates the total number of items (databases, tables, columns,
        or types) that will be dumped at the current depth.
    - `dump:partial`: Signals that a partial set of results has been retrieved for the
        current depth; more results may follow.
    - `dump:partial:partial`: Indicates that a partial result is being retrieved, with
        the current row and column being processed, allowing for real-time updates of
        the map as results are fetched.
    - `dump:done`: Marks the completion of the dumping process for the current depth;
        all items at this level have been retrieved.
    - `iter:start`: Indicates the start of an iteration over items at a certain depth,
        such as iterating over all databases or tables.
    - `iter:current`: Indicates the iteration's current position, i.e. which database or
        table is currently being processed.
    - `iter:done`: Indicates that the iteration over the current depth is complete.
    - `map`: Represents the finalization or update of the map with all gathered results,
        typically at the end of the process.
    """

    method: Method
    """The injection method to use to retrieve the results.
    """
    map: Map
    """Contains every part of the schema that has been dumped."""

    def __init__(self, method: Method):
        super().__init__()
        self.map = Map()
        self.method = method
        self.log = logging.logger(self.__module__)

    async def fetch(
        self,
        depth: MapDepth,
        *,
        database: str = None,
        table: str = None,
        column: str = None,
        listener: Optional[Listener] = None,
    ) -> Map:
        """Fetches new schema information.

        Args:
            depth (str): Kind of structure information to fetch: `database`,
                `table`, or `column`.
            database (str): Filter for database names
            table (str): Filter for table names
            column (str): Filter for column names
            listener (Listener): A listener that receives map events.

        Filters wildcards are available:

        - `*` means "any characters"
        - `?` means "any character"

        Examples:

            Fetches columns for table `wordpress.wp_users`:

                await mapper.fetch("columns", database="wordpress", table="wp_users")

            Fetches every table in  database `test`:

                await mapper.fetch("tables", database="test")

            Fetches every table that contains a column which contains `passw`:

                await mapper.fetch("tables", column="*passw*")
        """
        state = MapState()
        if listener:
            listener.link(state)
        ctx = state.context

        self.log.info(
            f"Fetching {depth!r} with filters: {database=}, {table=}, {column=}"
        )
        if depth not in MapDepth.__args__:
            depths = ", ".join(MapDepth.__args__)
            raise ValueError(f"Unknown map depth {depth!r}, allowed values: {depths}")

        try:
            map = await self._fetch(
                depth, database=database, table=table, column=column, ctx=ctx
            )
        except asyncio.CancelledError as e:
            self.log.error("Execution interrupted")
            ctx.notify("exception", e)
            raise
        except BaseException as e:
            ctx.notify("exception", e)
            self.log.exception("An exception occurred while fetching map")
            raise

        self.map.update(map)
        self.log.info(f"Results\n{map}")

        ctx.notify("map", map)
        return map

    def _is_filter_exact(self, filter) -> bool:
        return filter and "*" not in filter and "?" not in filter

    def _build_condition(
        self, field: Node, filter: str
    ) -> Comparison | WordedComparison:
        # If the filter does not have wildcards, we might as well use the
        # equality operator
        if self._is_filter_exact(filter):
            return field == filter
        filter = (
            filter.replace("_", "\\_")
            .replace("%", "\\%")
            .replace("?", "_")
            .replace("*", "%")
        )
        return field.like(filter)

    @abstractmethod
    async def _fetch(self, depth: MapDepth, ctx: Context, **filters: str) -> Map:
        """Dumps database, table or column names matching filters."""


class MetadataMapper(Mapper):
    """Mapper for DBMSs which posess meta tables containing database, table and
    column information, such as MySQL.

    Queries metatables to fetch databases, tables, columns and types.
    """

    FIELD_TYPE = TextType(
        charset=string.ascii_letters + string.digits + "_-", size=IntType(min=1, max=64)
    )
    TYPE_TYPE = TextType(
        charset=string.ascii_letters + string.digits + "_-()'",
        size=IntType(min=1, max=64),
    )
    """Standard type for metadata.
    """
    TABLES = {
        "database": "information_schema.tables",
        "table": "information_schema.tables",
        "column": "information_schema.columns",
        "type": "information_schema.columns",
    }
    """Tables that contains information about the databases, tables, or columns.
    """
    COLUMNS = {
        "database": Identifier("table_schema", type=FIELD_TYPE),
        "table": Identifier("table_name", type=FIELD_TYPE),
        "column": Identifier("column_name", type=FIELD_TYPE),
        "type": Identifier("data_type", type=TYPE_TYPE),
    }
    """Fields that contain the name of the database, table, or column.
    """

    def __init__(self, method: Method):
        super().__init__(method)
        assert all(isinstance(column, Node) for column in self.COLUMNS.values())

    async def _fetch(self, depth: MapDepth, ctx: Context, **filters: str) -> Map:
        """Dumps database, table or column names matching filters."""
        map = Map()

        dbs_ctx = ctx.with_(depth="databases")

        databases = await self.fetch_depths(["databases"], filters, dbs_ctx)
        map.radd(databases)

        if depth == "databases":
            return map

        dbs_ctx.notify("iter:start", count=len(databases))

        for (database,) in databases:
            dbs_ctx.notify("iter:current", database=database)
            tables_ctx = ctx.with_(depth="tables", database=database)
            database_filters = filters | {"database": database}

            tables = await self.fetch_depths(
                ["tables"], database_filters, ctx=tables_ctx
            )
            map.radd(database, tables)

            if depth == "tables":
                continue

            tables_ctx.notify("iter:start", count=len(tables))

            for (table,) in tables:
                tables_ctx.notify("iter:current", table=table)
                table_emitter = tables_ctx.with_(depth="columns", table=table)

                table_filters = database_filters | {"table": table}

                vdepth = ["columns", "types"] if depth == "types" else ["columns"]
                columns = await self.fetch_depths(vdepth, table_filters, table_emitter)
                map.radd(database, table, columns)

            tables_ctx.notify("iter:done")

        dbs_ctx.notify("iter:done")
        return map

    def _are_all_filters_exact(
        self, max_depth: MapDepth, filters: dict[str, str]
    ) -> bool:
        """Returns whether all filters up to `max_depth` are exact."""
        # NOTE We do not care if filters for deeper levels are not exact, as we don't
        # need the values of these levels, we just need to know if they exist or not
        # For instance, when retrieving tables, we can have an inexact filter for
        # columns
        for item in MapDepth.__args__:
            exact_filters = self._is_filter_exact(filters.get(item[:-1]))
            if item == max_depth or not exact_filters:
                break
        return exact_filters

    async def fetch_depths(
        self, depths: list[MapDepth], filters: dict[str, str], ctx: Context
    ) -> list[list[str]]:
        """Retrieves part of the schema."""

        if data := ctx.restore("part"):
            return data.data

        ctx.notify("dump:start")

        max_depth = max(depths, key=lambda depth: MapDepth.__args__.index(depth))
        self.log.debug(f"Fetching {max_depth!r} with filters: {filters}")

        singular_depths = [depth[:-1] for depth in depths]

        # What table should be used ? We use the deepest table that has a filter
        table = next(
            d
            for d in reversed(MapDepth.__args__)
            if filters.get(d[:-1]) or max_depth == d
        )[:-1]

        columns = [self.COLUMNS[singular_depth] for singular_depth in singular_depths]
        table = self.TABLES[table]

        conditions = []

        for name, filter in filters.items():
            if not filter:
                continue
            condition = self._build_condition(self.COLUMNS[name], filter)
            conditions.append(condition)

        query = Query(table)

        if conditions:
            conditions = functools.reduce(operator.__and__, conditions)
            query = query.where(conditions)

        # Are all filters exact ? If so, we don't need to retrieve the values, just
        # make sure they exist

        if self._are_all_filters_exact(max_depth, filters):
            query = Query().columns(query.columns(Count()) != 0)
            self.method.compiler.wrap(query)
            results = await self.method.fetch(query)

            if results.data[0][0]:
                results = [
                    [filters[singular_depth] for singular_depth in singular_depths]
                ]
            else:
                results = []
        # Otherwise, dump results
        else:
            query = query.columns(*columns).distinct()
            results = await self.method.fetch(query, listener=MapQueryListener(ctx))
            results = results.data

        ctx.notify("dump:done", results=results)
        return results


@dataclass
class MapQueryListener(Listener):
    """A listener for metadata queries."""

    map_ctx: Context

    def on_partial(self, row: int, column: int, partial: PartialCell, **_) -> None:
        try:
            row = [self.state.results[row][i] for i in range(column)]
        except KeyError:
            return
        row.append(partial)
        self.map_ctx.notify("dump:partial:partial", results=[row])

    def on_bounds(self, bounds: tuple[int, int], **kwargs) -> None:
        count = bounds[1] - bounds[0]
        self.map_ctx.notify("dump:count", count=count)

    def on_rows(self, rows: list, **kwargs) -> None:
        """Receives rows from the query and updates the map."""
        self.map_ctx.notify("dump:partial", results=rows)
