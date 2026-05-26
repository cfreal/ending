"""A "fake" compiler that displays SQL nodes in a human-readable way.
Use `ending.util.humanized.node` instead of using this compiler directly.
"""

from ending.ast import *
from ending.ast import Node
from ending.db.generic import Compiler, parenthesize
from ending.util import quoting

__all__ = ["HumanizedCompiler", "humanized_compiler"]


class HumanizedCompiler(Compiler):
    """A compiler that renders queries in an human readable way.
    Used to output more readable versions of nodes. This compiler will fail to proceed
    if a node type is unknown (i.e. ColonCast). Use `ending.util.humanized.node` to
    avoid such cases, as it will do best-effort to render the node.
    """

    def __init__(self):
        super().__init__(quote=quoting.singlequote, encoding="iso-8859-1")

    def serialize(self, node: Node) -> Node:
        raise RuntimeError("This method should not get called")

    def _adjust_column(self, column: Node) -> Node:
        raise RuntimeError("This method should not get called")

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
            b.append(" DISTINCT")

        b.append(" " + self.compile(List[Value](q.columns)))

        if q.table:
            b.append(f" FROM {q.table}")

        if q.where:
            b.append(f"\nWHERE {q.where}")

        if q.order:
            b.append(f" ORDER BY {q.order}")

        if q.limit:
            b.append(f"\nLIMIT {q.limit.start}, {q.limit.count}")

        query = "".join(b)

        if "p" in s:
            return f"({query})"
        return query

    @parenthesize
    def compile_List(self, lst: List, s: str) -> str:
        return ", ".join(f"{item:p}" for item in lst.items)


humanized_compiler = HumanizedCompiler()
