from __future__ import annotations

import asyncio
from argparse import Namespace
from collections import namedtuple
from typing import Any

from rich.align import Align
from rich.console import Console, ConsoleOptions, RenderResult
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ending.cli.design import Design, DesignDirectory
from ending.cli.misc import *
from ending.cli.misc import execution_interrupted_panel
from ending.cli.progress import (
    BarColumn,
    CompletedTotalColumn,
    TextColumn,
    TimeElapsedColumn,
)
from ending.db.generic.map import Map, MapDepth
from ending.struct.metadata import Listener, Context, Listener, MapState
from ending.struct.resultset import PartialCell
from ending.util.randomized import filename as randomized_filename

__all__ = ["do_map"]


def _save_map(map: Map, prefix: str, is_partial: bool) -> None:
    """Saves the map and displays the result along with the storage path."""
    map.store(prefix)

    panel = Panel(
        title="Map",
        renderable=map.tree(),
        border_style="results",
    )
    console.print(panel)
    storage_panel(prefix, is_partial=is_partial)


async def do_map(design_dir: DesignDirectory, ns: Namespace) -> None:
    Design = load_design_or_exit(design_dir)
    check_design_configured(Design)
    options = {}
    design = Design(options)
    await design.setup()
    await design.set_configuration()

    if ns.show:
        with status("Loading map..."):
            full = Map()
            for sub in design_dir.get_sub_path("map").glob("*.csv"):
                full.update(Map.load(sub))
        full = full.filter(
            ns.level,
            database=ns.database,
            table=ns.table,
            column=ns.column,
        )
        console.print(full)
        return

    prefix = ns.output or design_dir.get_sub_path("map") / randomized_filename()
    listener = MapStateListener(design)

    try:
        map: Map = await design.mapper.fetch(
            ns.level,
            database=ns.database,
            table=ns.table,
            column=ns.column,
            listener=listener,
        )
    except asyncio.CancelledError:
        _try_save_results(prefix, listener)
        execution_interrupted_panel()
    except BaseException as e:
        _try_save_results(prefix, listener)
        error_panel("Unhandled exception")
        traceback_panel()
    else:
        listener.stop()
        _save_map(map, prefix, is_partial=False)
    finally:
        console.show_cursor(True)
        await design.teardown()


def _try_save_results(prefix: Any, listener: MapStateListener) -> None:
    """Stops the listener and tries to save the partial results, if any."""
    listener.stop()

    if not listener.map:
        return

    try:
        _save_map(listener.map, prefix, is_partial=True)
    except Exception as e:
        error_panel(f"An error occurred while saving partial results")


class LiveMap(Map):
    """A map that only displays elements that fit in the console."""

    SPACE: int = 7
    """Number of rows to leave empty for other elements: the `LiveMap` will use at most
    `nb_columns`-`SPACE` elements.
    """
    max_elements: int
    """Maximum number of elements to display."""

    def __init__(self) -> None:
        self.max_elements = 0
        super().__init__()

    def _delete_partial(self, *elements: str | PartialCell) -> bool:
        for i, element in reversed(list(enumerate(elements))):
            if isinstance(element, PartialCell):
                break
        else:
            return True

        items = self.items

        partial = elements[i]

        for element in elements[:i]:
            items = items.setdefault(element, {})

        if partial.done():
            if i == 3:
                items["type"] = partial.get()
            else:
                items.pop(partial, None)
                items[partial.get()] = {}
            return False

        if i == 3:
            return True
        return partial not in items

    def add(
        self, db: str, table: str = None, column: str = None, type: str = None
    ) -> None:
        """Adds a database, table, column to the map."""
        if self._delete_partial(db, table, column, type):
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

    def _get_oldest_tuple(self, map: dict) -> None:
        key = next(iter(map))
        return key, map[key]

    def _remove_oldest_element(self) -> None:
        databases = self.items

        # Database level

        key, tables = self._get_oldest_tuple(databases)

        if not tables:
            del databases[key]
            return

        # Table level

        key, columns = self._get_oldest_tuple(tables)

        if not columns:
            del tables[key]
            return

        # Column level

        key, _ = self._get_oldest_tuple(columns)
        del columns[key]

    def remove_oldest_elements(self) -> None:
        """Removes the oldest elements from the tree until the total number of elements
        is equal to `LiveMap.max_elements`.
        """
        total = sum(
            1 + sum(1 + len(columns) for columns in tables.values())
            for tables in self.items.values()
        )
        if self.max_elements > 0:
            while total >= self.max_elements:
                self._remove_oldest_element()
                total -= 1

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        max_height = options.max_height or options.size.height
        max_rows = max_height - self.SPACE
        self.max_elements = max(0, max_rows)
        self.remove_oldest_elements()
        return super().__rich_console__(console, options)


class MapStateListener(Listener):
    """Sets up a rich layout and updates it with the live results."""

    state: MapState
    layout: Layout
    map: LiveMap
    live: Live

    def __init__(self, design: Design):
        self.design = design
        self.table = None
        self.map = LiveMap()
        self.build_layout()

    def build_layout(self) -> None:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        self.map_panel = Panel(
            Align("-", align="center", vertical="middle"),
            title="Map",
            border_style="results",
        )
        self.status = Spinner("dots", style="status.spinner", text="Initializing")
        self.progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(None),
            CompletedTotalColumn(),
            TextColumn("[progress.percentage]{task.percentage:.0f}%"),
        )
        prefixes = {
            "databases": "[blue]◼[/] ",
            "tables": "[red]◼[/] ",
            "columns": "[green]◼[/] ",
        }
        self.progresses = {
            depth: self.progress.add_task(f"{prefixes[depth]}{depth.title()}", total=0)
            for depth in MapDepth.__args__[:-1]
        }
        self.progresses["types"] = self.progresses["columns"]
        grid.add_row(self.map_panel)
        grid.add_row(
            Panel(
                self.progress,
                title="Progress",
                border_style="results",
            )
        )
        grid.add_row(Align.center(self.status))
        self.live = Live(
            Align(grid, align="center"), console=console, screen=False, transient=True
        )
        self.live.start()
        self.map_panel.renderable.renderable = self.map

    def _progress_update(self, depth: MapDepth, **kwargs: Any) -> None:
        """Updates the progress for the given depth."""
        self.progress.update(self.progresses[depth], **kwargs)

    def _progress_ready(self, depth: MapDepth, total: int = None) -> None:
        """Marks the task as ready."""
        self._progress_update(depth, visible=True, completed=0, total=total)

    def _progress_advance(self, depth: str, nb: int) -> None:
        """Advances the progress for the given depth."""
        self._progress_update(depth, advance=nb)

    def _progress_hide(self, depth: str) -> None:
        """Hides the progress for the given depth."""
        self._progress_update(depth, visible=False, total=None)

    def _progress_reset(self, depth: str) -> None:
        """Resets the progress for the given depth."""
        self._progress_update(depth, completed=0)

    def _update_map(self, info: namedtuple) -> None:
        match info.depth:
            case "databases":
                self.map.radd(info.results)
            case "tables":
                self.map.radd(info.database, info.results)
            case "columns" | "types":
                self.map.radd(info.database, info.table, info.results)

    def on(self, event: str, context: Context) -> None:
        """Handles map events and updates the UI accordingly."""
        ctx = context.tuple()

        match event:
            case "dump:start":
                match ctx.depth:
                    case "databases":
                        text = "Dumping databases"
                        self._progress_ready("databases")
                        self._progress_hide("tables")
                        self._progress_hide("columns")
                    case "tables":
                        text = f"Dumping tables from [b]{ctx.database}[/]"
                        self._progress_ready("tables")
                        self._progress_hide("columns")
                    case "columns" | "types":
                        text = f"Dumping columns from [b]{ctx.database}[/].[b]{ctx.table}[/]"
                        self._progress_ready("columns")
                self.status.text = Text.from_markup(text)
            case "dump:count":
                self._progress_ready(ctx.depth, total=ctx.count)
            case "dump:partial:partial":
                self._update_map(ctx)
            case "dump:partial":
                self._update_map(ctx)
                self._progress_advance(ctx.depth, nb=len(ctx.results))
            case "dump:done":
                self._update_map(ctx)
            case "iter:start":
                self._progress_ready(ctx.depth, total=ctx.count)
                match ctx.depth:
                    case "databases":
                        self._progress_hide("tables")
                        self._progress_hide("columns")
                    case "tables":
                        self._progress_hide("columns")
            case "iter:current":
                self._progress_advance(ctx.depth, nb=1)
            case "iter:done":
                pass
            case "map":
                self.map.update(ctx.map)
                self.live.stop()

    def stop(self) -> None:
        """Stops live display."""
        self.live.stop()
