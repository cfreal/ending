from rich.progress import *

from ending.util.humanized import size as humanized_size

__all__ = [
    "progress_columns",
    "CompletedTotalColumn",
    "PanelColumn",
    "TimeElapsedColumn",
    "TimeRemainingColumn",
]


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
    """A progress column which wraps a standard column in a `Panel`."""

    def __init__(self, *columns, **kwargs) -> None:
        super().__init__()
        self.columns = columns
        self.kwargs = kwargs

    def render(self, task):
        table = Table.grid(padding=(0, 1))
        table.add_row(*(column.render(task) for column in self.columns))
        return Panel(table, **self.kwargs)


progress_columns = [
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
