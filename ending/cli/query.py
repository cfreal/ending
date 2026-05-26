from __future__ import annotations

import asyncio
import string
import textwrap
from argparse import Namespace
from dataclasses import dataclass
from typing import Any

from rich.align import Align
from rich.console import Console, ConsoleOptions
from rich.live import Live
from rich.panel import Panel
from rich.progress import *
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ending.ast import *
from ending.cli.design import Design, DesignDirectory
from ending.cli.misc import *
from ending.exception import InjectionError
from ending.struct.metadata import Listener, QueryState
from ending.struct.resultset import PartialCell, ResultSet
from ending.util.humanized import node as humanized_node
from ending.util.humanized import size as humanized_size
from ending.util.randomized import filename as randomized_filename
from ending.util.typing import Row
from ending.validation import QueryValidator, ValidationError

__all__ = ["do_query"]


MAX_LINES_IN_FINAL_TABLE = 1000
"""Do not display more than this amount of rows in the final table."""


def _save_results(prefix: str, partial: bool, listener: QueryStateListener) -> None:
    msg_partial = partial and "partial " or ""

    try:
        results = listener.table.get_final_results()
        results.store(prefix)
    except Exception:
        error_panel(f"An error occurred while saving {msg_partial}results")
        traceback_panel()
    else:
        storage_panel(prefix, partial)


async def do_query(design_dir: DesignDirectory, ns: Namespace) -> None:
    Design = load_design_or_exit(design_dir)
    check_design_configured(Design)

    # Build query from arguments

    if not ns.fields and not ns.dump_count:
        raise CLIError(
            "Please specify which columns to dump ([i]--fields[/], [i]-f[/])"
        )

    query = Query(ns.table)

    if ns.fields:
        query = query.columns(*ns.fields)

        # The user provided type information
        if ns.field_types:
            if len(ns.field_types) != len(ns.fields):
                raise CLIError(
                    f"Received {len(ns.field_types)} field types for {len(ns.fields)} fields."
                )

            type_map = {
                "T": TextType,
                "I": IntType,
                "B": BoolType,
                "X": BlobType,
                "U": UnknownType,
                # Custom text types
                ## Hexadecimal
                "H": lambda: TextType(charset=string.hexdigits),
                ## Base64
                "6": lambda: TextType(
                    charset=string.hexdigits + string.ascii_letters + "=/+_-"
                ),
            }

            for tpe in ns.field_types.upper():
                if tpe not in type_map:
                    raise CLIError(f"Unknown field type: {tpe}")

            try:
                query = query.columns(
                    *(
                        field.with_type(type_map.get(tpe)())
                        for field, tpe in zip(query.q.columns, ns.field_types.upper())
                    )
                )
            except KeyError:
                raise CLIError(f"Invalid type description: {ns.field_types}")

    if ns.where:
        query = query.where(*ns.where)

    if ns.start is not None and ns.count is not None:
        query = query.limit(ns.start, ns.count)
    else:
        query = query.limit(ns.start or ns.count)

    if ns.dump_count:
        if ns.fields:
            count = Count(query.q.columns, ns.distinct)
        elif ns.distinct:
            raise CLIError(
                "Error: [i]--distinct[/] has no effect without [i]--fields[/]"
            )
        else:
            count = Count()
        query = query.columns(count)
    elif ns.distinct:
        query = query.distinct()

    if ns.order:
        query = query.order(ns.order, reverse=ns.order_reverse)

    # Run or validate query

    options = {}
    design = Design(options)

    await design.setup()
    await design.set_configuration()

    status = ConsoleLiveStatus()

    # Validate the query
    if ns.validate:
        console.print()
        status.section("Query validation", "Validating query")
        try:
            await QueryValidator(design.method, query, status=status).validate()
        except ValidationError as e:
            display_validation_error(e)
        else:
            message_success("Query [b]OK[/b]")
        finally:
            status.done()

        await design.teardown()
        return

    # Run the query

    state = StateSaver.load(ns) or QueryState()
    listener = QueryStateListener(design)

    prefix = ns.output or design_dir.get_sub_path("queries") / randomized_filename()

    try:
        results = await design.method.fetch(query, listener=listener, state=state)
    # Display SQL injection error cleanly
    except InjectionError as e:
        listener.stop()
        _save_results(prefix, True, listener)

        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        grid.add_row(e.message)
        if e.payload:
            grid.add_row(Rule())
            grid.add_row(
                Syntax(
                    textwrap.fill(humanized_node(e.payload), 96), "sql", padding=(0, 1)
                )
            )

        error_panel(grid)
    # Handle Ctrl-C
    except (GeneratorExit, asyncio.CancelledError):
        listener.stop()
        _save_results(prefix, True, listener)
        StateSaver.save(ns, listener.state)
        execution_interrupted_panel()
    # Display full stacktrace for other exceptions
    except BaseException as e:
        listener.stop()
        _save_results(prefix, True, listener)
        error_panel("Unhandled exception")
        traceback_panel()
    # In case this went fine, just save the results and forget the state
    else:
        listener.stop()
        _save_results(prefix, False, listener)
        StateSaver.delete()
    finally:
        console.show_cursor(True)
        await design.teardown()


class CompletedTotalColumn(ProgressColumn):
    """Displays the total in a human-readable format."""

    def get_string_for_nb(self, nb: int):
        return humanized_size(nb)

    def render(self, task) -> Text:
        """Calculate common unit for completed and total."""
        completed = self.get_string_for_nb(task.completed)
        total = self.get_string_for_nb(task.total)
        return Text(f"{completed}/{total}", style="progress.download")


class PanelColumn(ProgressColumn):
    """A progress column wrapped in a `Panel`."""

    def __init__(self, *columns, **kwargs) -> None:
        super().__init__()
        self.columns = columns
        self.kwargs = kwargs

    def render(self, task) -> Panel:
        table = Table.grid(padding=(0, 1))
        table.add_row(*(column.render(task) for column in self.columns))
        return Panel(table, **self.kwargs)


_progress_columns = [
    PanelColumn(
        BarColumn(None),
        title="Progress",
        border_style="progress",
    ),
    PanelColumn(
        CompletedTotalColumn(),
        TextColumn("[progress.percentage]{task.percentage:.0f}%"),
        title="Done",
        border_style="progress",
    ),
    PanelColumn(
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        title="Timing",
        border_style="progress",
        width=len("│ 0:00:39 0:00:54 │"),
    ),
]


@dataclass
class LiveResultsTable:
    state: QueryState

    def for_height(self, max_lines: int) -> Table:
        """Returns a table that contains as many results as possible, while fitting in
        at most `max_lines` lines.
        """
        results = self.state.results
        nb_rows = len(results)

        # We'll add arrows at the top of the table to indicate that there are rows
        # that do not fit in the screen
        if nb_rows > max_lines:
            max_lines -= 1

        # We'll add arrows at the bottom of the table to indicate that there are rows
        # that have not been fetched yet
        if self.state.bounds and results:
            last_row_idx = self.state.bounds[1] - self.state.bounds[0] - 1
            bottom_arrows = last_row_idx > max(results.keys())
        else:
            bottom_arrows = True

        if bottom_arrows:
            max_lines -= 1

        results = self.state.get_partial_results(last=max_lines, trim=True)

        if results is None:
            table = Spinner("hamburger")
        else:
            if max_lines < nb_rows:
                up_arrow = Text("▲", style="red", justify="center")
                results.data.insert(0, self.build_dummy_row(up_arrow))

            if bottom_arrows:
                down_arrow = Text("▼", style="red", justify="center")
                results.data.append(self.build_dummy_row(down_arrow))

            table = results.table()

        return table

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> Table:
        """Returns a table that contains as many results as possible, while fitting the
        current screen.
        """
        max_height = options.max_height or options.size.height
        table = self.for_height(max_height - 5 - 5)
        return table.__rich_console__(console, options)

    def get_final_results(self) -> ResultSet:
        """Returns a `ResultSet` that contains every result that has been obtained at
        this point, and (potentially) bottom arrows to indicate that some rows are still
        missing.
        """
        results = self.state.get_partial_results(trim=True)
        if len(results.data) < self.state.bounds[1] - self.state.bounds[0]:
            down_arrow = Text("▼", style="red", justify="center")
            results.data.append(self.build_dummy_row(down_arrow))
        return results

    def build_dummy_row(self, cell: Any) -> list[Any]:
        """Builds a row that contains the same value for each column, `cell`."""
        return [cell] * len(self.state.query.q.columns)

    def get_nb_fetched(self) -> int:
        """Returns the number of rows that have been completely fetched."""
        nb_columns = len(self.state.query.q.columns)
        return sum(
            not any(isinstance(c, PartialCell) for c in row)
            for row in self.state.results.values()
            if len(row) == nb_columns
        )


class QueryStateListener(Listener):
    """Sets up a rich layout and updates it with the live results."""

    state: QueryState
    live: Live
    progress: Progress
    table: LiveResultsTable
    design: Design

    def __init__(self, design: Design) -> None:
        self.design = design
        self.table = None
        self.build_layout()
        self._progress_task_id = None

    def set_table(self, table: LiveResultsTable) -> None:
        self.table = table
        self.results_panel.renderable.renderable = table

    def build_layout(self) -> None:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        self.results_panel = Panel(
            Align("-", align="center", vertical="middle"),
            title="Results",
            border_style="results",
        )
        self.progress = Progress(*_progress_columns)
        grid.add_row(self.results_panel)
        grid.add_row(self.progress)
        self.live = Live(
            grid,
            console=console,
            screen=False,
            transient=True,
            refresh_per_second=10,
        )
        self.live.start()

    def _results_panel(self, renderable: RenderableType) -> Panel:
        return Panel(
            renderable,
            title="Results",
            border_style="results",
        )

    def on_query(self, query: Query, **_) -> None:
        self.set_table(LiveResultsTable(self.state))

    def on_bounds(self, bounds: tuple[int, int], **_) -> None:
        total_rows = bounds[1] - bounds[0]
        if self._progress_task_id is not None:
            raise ValueError("This should not happen")
        completed = self.table.get_nb_fetched()
        self._progress_task_id = self.progress.add_task(
            "", total=total_rows, completed=completed
        )

    def on_rows(self, rows: list[Row], **_) -> None:
        self.progress.update(self._progress_task_id, advance=len(rows))

    def on_exception(self, exception: BaseException, **_) -> None:
        pass

    def stop(self) -> None:
        """Stops live display and saves the results."""
        self.live.stop()

        if not self.table:
            return

        self.results_panel.renderable.renderable = self.table.for_height(
            MAX_LINES_IN_FINAL_TABLE
        )
        console.print(self.live.renderable)
