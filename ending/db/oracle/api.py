import asyncio
import functools
import string
from functools import reduce

from ending.ast import *
from ending.ast import Alias, Concatenation, Length, Node, Ord, Query
from ending.db import generic
from ending.exception import ConversionError, InjectionError
from ending.struct.metadata import Context
from ending.util import quoting, randomized

__all__ = [
    "Compiler",
    "SelectMethod",
    "TestMethod",
    "Mapper",
    "Features",
]


class Features(generic.Features):
    name = "Oracle"
    tautology = "bitand(6,2)=2"
    quoters = [
        quoting.singlequote,
        quoting.pipes_chr,
    ]
    error_condition: str = "(CASE WHEN {condition} THEN 1/0 ELSE 1 END)={result}"


class Compiler(generic.Compiler):
    """Compiler for Oracle."""

    def _adjust_column(self, column: Node) -> Node:
        if isinstance(column.metadata.type, UnknownType):
            return Cast(column, "VARCHAR(4000)", type=TextType())
        return column

    def serialize(self, node: Node) -> Node:
        match node.metadata.type:
            case TextType():
                return node
            case BlobType():
                return Hex(node)
            case BoolType() as tpe:
                cases = ((Value(True), Value("1")), (Value(False), Value("0")))
                return Case(node, cases, type=tpe.to_texttype())
            case IntType() as tpe:
                return Cast(node, "VARCHAR(100)", type=tpe.to_texttype())
            case tpe:
                raise ConversionError(f"Cannot serialize type: {type(tpe).__name__}")

    def compile_Query(self, query: Query, s: str) -> str:
        b = []
        q = query.q
        assert len(q.columns) > 0, "Query contains no columns"

        # LIMIT implies a superquery
        if q.limit:
            rownum = Alias.randomized(Identifier("ROWNUM"))
            base_query = query.limit(None).columns(*q.columns, rownum)
            min, nb = q.limit.get_start_count()

            if nb == 1:
                conditions = rownum.alias == min + 1
            elif min == 0:
                conditions = rownum.alias < min + nb + 1
            else:
                # TODO Use between
                conditions = (rownum.alias > min) & (rownum.alias < min + nb + 1)

            super_query = base_query.super_query()
            # Add the conditions
            super_query = super_query.where(conditions)
            # Get rid of the extra column which represents ROWNUM
            super_query = super_query.columns(*super_query.q.columns[:-1])
            return super_query

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
        else:
            b.append(f"FROM DUAL")

        if q.where:
            b.append(f"WHERE {q.where}")

        if q.order:
            b.append(f"ORDER BY {q.order}")

        query = " ".join(b)

        if "p" in s:
            return f"({query})"

        return query

    def compile_Substring(self, substring: Substring, s) -> Function:
        if isinstance(substring.string.metadata.type, BlobType):
            # https://docs.oracle.com/en/database/oracle/oracle-database/21/arpls/DBMS_LOB.html
            # The argument order is (expr, size, offset)!
            return super().compile_Substring(substring, s, "UTL_RAW.SUBSTR")
        return super().compile_Substring(substring, s, "SUBSTR")

    def compile_Ord(self, ord: Ord, s):
        match ord.node.metadata.type:
            case BlobType():
                return Function["UTL_RAW.CAST_TO_BINARY_INTEGER"](ord.node)
            case _:
                return Function["ASCII"](ord.node)

    def compile_Length(self, length: Length, s) -> Function:
        match length.node.metadata.type:
            case BlobType():
                return Function["DBMS_LOB.GETLENGTH"](length.node)
            case _:
                return Function["LENGTH"](length.node)

    def compile_Hex(self, hex: Hex, s: str) -> str:
        if isinstance(hex.node.metadata.type, BlobType):
            # Returns the 32767 first bytes by default
            # https://docs.oracle.com/en/database/oracle/oracle-database/21/arpls/DBMS_LOB.html
            node = Function["DBMS_LOB.SUBSTR"](hex.node)
        else:
            node = hex.node
        return Function["RAWTOHEX"](node)

    def compile_Alias(self, alias: Alias, s: str) -> str:
        # Oracle does not support the AS keyword for table aliases
        # We keep it for column aliases, though
        if isinstance(alias.node, Query):
            node = f"({alias.node})"
        else:
            node = f"{alias.node:p}"
        return f"{node} {alias.alias}"

    def compile_Concatenation(self, concatenation: Concatenation, s: str) -> str:
        if not concatenation.nodes:
            return Value("")
        return "||".join(f"{node:p}" for node in concatenation.nodes)

    def compile_Value(self, value, s) -> str:
        match value:
            case Value(True):
                return "TRUE"
            case Value(False):
                return "FALSE"
            case _:
                return super().compile_Value(value, s)


class AdjustForBlobMixin(generic.Method):
    async def _column_is_errored(self, base: Query, column: Node) -> bool:
        """Returns whether the column is errored or not. If the column is errored,
        IS NULL and IS NOT NULL will yield the same result, or an exception will be
        raised.
        """
        try:
            results1, results2 = await asyncio.gather(
                self.fetch(base.columns(IsNull(column))),
                self.fetch(base.columns(IsNull(column, negate=True))),
            )
        except InjectionError:
            return True
        else:
            if results1.data[0][0] == results2.data[0][0]:
                return True
        return False

    async def adjust_query(self, query: Query, ctx: Context):
        # If a column's type is set to unknown, and it is in reality a BLOB, it is hard
        # to cast it to a string or anything readable. To make sure any column type
        # can be read, we check if any unknown column is a BLOB, and cast it
        # accordingly.
        unknown_columns = [
            column
            for column in query.q.columns
            if isinstance(column.metadata.type, UnknownType)
        ]
        if not unknown_columns:
            return await super().adjust_query(query, ctx=ctx)

        base = query.where(None).order(None).limit(1)

        rawtohex = Function["RAWTOHEX"]

        columns = [Length(rawtohex(column)) for column in unknown_columns]
        added_columns = functools.reduce(
            (lambda x, y: ArithmeticOperation(x, "-", y)), columns
        )

        # Find out which columns are blobs and cast accordingly
        if await self._column_is_errored(base, added_columns):
            new_columns = []
            for column in query.q.columns:
                if isinstance(
                    column.metadata.type, UnknownType
                ) and await self._column_is_errored(base, rawtohex(column)):
                    new_columns.append(column.with_type(BlobType()))
                else:
                    new_columns.append(column)
            query = query.columns(*new_columns)

        return await super().adjust_query(query, ctx=ctx)


class SelectMethod(AdjustForBlobMixin, generic.HexSelectMethod):
    pass


class TestMethod(AdjustForBlobMixin, generic.ByteSumMixin, generic.TestMethod):
    async def adjust_query(self, query: Query, ctx: Context):
        # When fetching several columns separately, there is no garantee the
        # results will be in the same order; we have to specify an order if it
        # has not been done
        if self._needs_order_by(query):
            # Oracle does not support ordering by BLOBs, so in case of an unknown or
            # blob column type, it needs to converted to something sortable.
            ordering_columns = [
                (
                    column
                    if not isinstance(column.metadata.type, UnknownType)
                    else self.compiler._adjust_column(column)
                )
                for column in query.q.columns
                if not isinstance(column.metadata.type, BlobType)
            ]
            query = query.order([Order(column) for column in ordering_columns])

        return await super().adjust_query(query, ctx=ctx)

    async def fetch_bool(self, expr: Node, ctx: Context) -> bool:
        if isinstance(expr, Query) and not isinstance(expr.metadata.type, BoolType):
            expr = expr == True
        return await super().fetch_bool(expr, ctx=ctx)


class Mapper(generic.MetadataMapper):
    FIELD_TYPE = TextType(
        charset=string.ascii_uppercase + string.digits + "_-#",
        size=IntType(min=1, max=128),
    )
    TYPE_TYPE = TextType(
        charset=string.ascii_uppercase + string.digits + "_-()'",
        size=IntType(min=1, max=128),
    )
    """Standard type for metadata.
    """
    TABLES = {
        "database": "all_tables",
        "table": "all_tables",
        "column": "all_tab_columns",
        "type": "all_tab_columns",
    }
    """Tables that contains information about the databases, tables, or columns.
    """
    COLUMNS = {
        "database": Identifier("owner", type=FIELD_TYPE),
        "table": Identifier("table_name", type=FIELD_TYPE),
        "column": Identifier("column_name", type=FIELD_TYPE),
        "type": Identifier("data_type", type=TYPE_TYPE),
    }
    """Fields that contain the name of the database, table, or column.
    """
