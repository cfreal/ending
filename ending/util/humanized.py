"""Converts various data into human-readable form."""

import builtins
import copy
from dataclasses import fields, replace
from typing import Any

from rich.align import Align
from rich.console import ConsoleRenderable, RenderableType, RichCast
from rich.text import Text

from ending.ast import Node
from ending.exception import CompilerNotSetError

__all__ = ["cell", "rich_cell", "node", "size"]


def size(value: int, min_wrap=10000) -> str:
    suffixes = ["", "K", "M", "G", "T", "P", "E", "Z", "Y"]

    if value < min_wrap:
        return str(value)

    order = 0
    order_size = 1000
    while value > order_size:
        value /= order_size
        order += 1
    precision = 0 if order == 0 else 1
    return f"{value:,.{precision}f}{suffixes[order]}"


def cell(value: Any) -> str:
    """Returns the best human-readable representation of `value` as a string."""
    match value:
        case str():
            return value
        case bytes():
            try:
                return value.decode()
            except UnicodeDecodeError:
                return str(value)
        case Node():
            try:
                return str(value)
            except CompilerNotSetError:
                return repr(value)
        case builtins.Ellipsis:
            return "..."
        case _:
            try:
                return str(value)
            except:
                return repr(value)


def rich_cell(value: Any) -> RenderableType:
    """Returns the best human-readable representation of `value` as a `rich`
    renderable, to be put in a `rich.table.Table`.
    """

    MAX_WIDTH = 1000

    match value:
        case ConsoleRenderable() | RichCast():
            return value
        case builtins.Ellipsis:
            return Align("…", "center", style="italic yellow")
        case True:
            return Align.center("✓", style="green")
        case False:
            return Align.center("✕", style="red")
        case int() | float():
            return Align.right(str(value), style="#9AA899")
        case str() | bytes():
            value = cell(value)
            if len(value) > MAX_WIDTH:
                value = value[: MAX_WIDTH - 1] + "…"
            return Text(value)
        case None:
            return Align.center("None", style="#4A7B9D")
        case _:
            if "Partial" in type(value).__name__:
                return type(value).__name__
                # return value
                # if value.elements:
                #     if isinstance(list(value.elements.values())[0],str):
                #         jointure = ""
                #         miss = "+"
                #     else:
                #         jointure = b""
                #         miss = b"_"
                #     return Text(cell(jointure.join(value.elements.get(i, miss) for i in range(value.length))))
                # return "?"
            raise ValueError(f"Cannot render type {type(value).__name__} for {value!r}")


def node(value: Node) -> str:
    """Returns the best human-readable representation of an SQL `Node`."""
    # The function will first try to display the node with the humanized compiler, then
    # with the original node's compiler, if set, and finally use its repr if it cannot
    # get a proper text representation

    # To compile the node with another compiler, we need to copy the Nodes and
    # keep the other types as is.
    def maybe_copy(value: Any) -> Any:
        if isinstance(value, tuple):
            return tuple(maybe_copy(v) for v in value)

        if isinstance(value, Node):
            vfields = {k.name: getattr(value, k.name) for k in fields(value)}
            return replace(value, **{k: maybe_copy(v) for k, v in vfields.items()})

        return value

    from ending.util.humanized_compiler import humanized_compiler

    copied_value = maybe_copy(value)

    try:
        humanized_compiler.wrap(copied_value)
        return str(copied_value)
    except:
        pass

    try:
        return str(value)
    except:
        pass

    return repr(value)
