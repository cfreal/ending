"""Generic typing lexic."""

from __future__ import absolute_import

from typing import *

from ending.ast import Node

__all__ = [
    "Cell",
    "Row",
    "Table",
    "Bounds",
    "QuoteCallable",
    "InjectType",
    "InjectFor",
    "InjectForBytes",
    "InjectForBool",
    "InjectForNone",
]

Cell = str | bytes | int | bool | None
"""A single cell in a result row."""
Row = List[Cell]
"""A result row."""
Table = List[Row]
"""A result table."""
Bounds = Tuple[int, int]
"""A pair of (offset, limit)."""
QuoteCallable = Callable[[str], str]
"""A callable that quotes identifiers for a specific SQL dialect.
See `ending.util.quoting`.
"""

InjectType = TypeVar("InjectType", bytes, bool, None)
InjectFor = Callable[[Node], Awaitable[InjectType]]
InjectForBytes = InjectFor[bytes]
"""A coroutine that takes a Node as input and returns the bytes of the response body."""
InjectForBool = InjectFor[bool]
"""A coroutine that takes a Node as input and returns a boolean value."""
InjectForNone = InjectFor[None]
"""A coroutine that takes a Node as input and returns nothing."""
