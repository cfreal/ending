from __future__ import annotations

import base64
import csv
from abc import ABC
from dataclasses import dataclass
from typing import Generic, TypeVar

from rich import box
from rich.align import Align
from rich.console import Console
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ending.ast import BlobType, Query
from ending.struct.storable import Storable
from ending.util.humanized import cell as humanized_cell
from ending.util.humanized import rich_cell
from ending.util.misc import niter
from ending.util.typing import Cell
from ending.util.typing import Table as TableType

__all__ = [
    "ResultSet",
    "PartialCell",
    "StringPartialCell",
    "BytesPartialCell",
    "UnknownCell",
]


@dataclass(frozen=True)
class UnknownCell:
    """A cell whose value has not been determined."""

    def __str__(self) -> str:
        return "?"

    def __rich__(self) -> Align:
        return Align.center(Spinner("hamburger", style="unknown-cell"))


PT = TypeVar("PT", str, bytes)
"""Type for partial cells."""


class PartialCell(ABC, Generic[PT]):
    """A cell whose contents have not been entirely determined.
    It stores the expected length of the cell, and some of its parts.
    """

    JOINTURE: PT
    """Value that .join() gets called onto to merge parts."""
    PLACEHOLDER: PT
    """Value that is used in place of missing parts."""

    __slots__ = ["length", "parts"]

    def __init__(self, length: int):
        self.length: int = length
        self.parts: dict[int, PT] = {}

    def set(self, position: int, value: PT) -> None:
        if position < 0 or position >= self.length:
            raise IndexError(
                f"position {position} is out of bounds for length {self.length}"
            )

        self.parts[position] = value

    def done(self) -> bool:
        """Returns true if every part of the partial cell is known."""
        return len(self.parts) == self.length

    def get(self) -> PT:
        """Returns the contents of the cell. Missing parts are replaced by
        `PartialCell.PLACEHOLDER`.
        """
        return self.JOINTURE.join(
            self.parts.get(i, self.PLACEHOLDER) for i in range(self.length)
        )

    def matches(self, value: PT, prefix: bool = True) -> bool:
        """Returns `True` if the cell could have the same value as `value`."""
        if prefix:
            if self.length < len(value):
                return False
        elif self.length != len(value):
            return False

        return all(
            self.parts[i] == item
            for i, item in enumerate(niter(value, 1))
            if i in self.parts
        )

    def __str__(self) -> str:
        return humanized_cell(self.get())

    def __rich__(self) -> Text:
        return Text(str(self), style="partial-cell")


class StringPartialCell(PartialCell[str]):
    """A partial cell of type `str`."""

    JOINTURE: str = ""
    PLACEHOLDER: str = "·"


class BytesPartialCell(PartialCell[bytes]):
    """A partial cell of type `bytes`."""

    JOINTURE: bytes = b""
    PLACEHOLDER: bytes = b"."


class ResultSet(Storable):
    """Holds the results of an SQL query."""

    def __init__(self, query: Query, data: TableType):
        """
        Args:
            query (Query): The SQL query that yielded the results.
            data (TableType): the results.
        """
        self.query = query
        self.data = data

    def __str__(self) -> str:
        """Returns a human-readable representation of the result set.

        Example:

        ```
        ╭────┬────┬────╮
        │ a  │ b  │ c  │
        ├────┼────┼────┤
        │ a1 │ b1 │ c1 │
        │ a2 │ b2 │ c2 │
        ╰────┴────┴────╯
        ```
        """

        def newline(line):
            return line + "\n"

        def print_row(row):
            ok = " │ ".join(
                f"{formatter(cell):<{mc}}" for cell, mc in zip(row, max_for_column)
            )
            return newline(f"│ {ok} │")

        def print_delimiter(chars: str):
            left, middle, right = chars
            line = middle.join("─" * (mc + 2) for mc in max_for_column)
            return newline(f"{left}{line}{right}")

        nb_columns = len(self.query.q.columns)
        max_for_column = [0] * nb_columns
        formatter = humanized_cell
        result = ""

        for row in [self.query.q.columns] + self.data:
            for i, cell in enumerate(row):
                max_for_column[i] = max(max_for_column[i], len(formatter(cell)))

        result += print_delimiter("╭┬╮")
        result += print_row(self.query.q.columns)
        result += print_delimiter("├┼┤")
        result += "".join(print_row(row) for row in self.data)
        result += print_delimiter("╰┴╯")

        return result

    def take(self, n: int, ellipsis: bool = True) -> ResultSet:
        """Returns the first `n` rows of the result set.
        If ellipsis is True and there are more than `n` rows, the last row will be
        replaced by an ellipsis.
        """
        if ellipsis and len(self.data) > n:
            data = self.data[: n - 1] + [[...] * len(self.query.q.columns)]
        else:
            data = self.data[:n]

        return ResultSet(self.query, data)

    def table(self) -> Table:
        """Returns a `rich.table.Table` representing the result set."""
        columns = map(str, self.query.q.columns)
        table = Table(*columns, box=box.ROUNDED)

        for row in self.data:
            table.add_row(*map(rich_cell, row))

        return table

    def __rich__(self) -> Table:
        return self.table()

    def store_as_csv(self, path: str) -> None:
        """Saves the data as a CSV file."""

        with open(path, "w") as handle:
            writer = csv.writer(handle, escapechar="\\")
            columns = self.query.q.columns
            writer.writerow(map(str, columns))
            for row in self.data:
                writer.writerow(
                    [
                        (
                            "..."
                            if value is Ellipsis
                            else (
                                str(value)
                                if not isinstance(cell.metadata.type, BlobType)
                                else base64.b64encode(value).decode()
                            )
                        )
                        for cell, value in zip(columns, row)
                    ]
                )

    def store_as_rich(self, path: str) -> None:
        """Stores the object as it was displayed in the terminal."""
        from ending.cli.misc import build_console

        with open(path, "w") as handle:
            console = build_console(
                width=1024**2, force_terminal=True, emoji=False, file=handle
            )
            console.print(self.table())

    def column(self, index: int) -> list[Cell]:
        """Returns the results for the column at given index."""
        return [row[index] for row in self.data]
