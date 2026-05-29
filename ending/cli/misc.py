from __future__ import annotations

from argparse import Namespace
import pickle
import sys
from typing import Any, NoReturn

from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.spinner import SPINNERS
from rich.text import Text
from rich.theme import Theme
from rich.traceback import Traceback

from ending.cli.design import Design, DesignDirectory, DesignLoadException
from ending.struct.livestatus import LiveStatus
from ending.struct.metadata import State
from ending.util.misc import ENDING_PATH
from ending.validation import ValidationError

__all__ = [
    "CLIError",
    "ConsoleLiveStatus",
    "theme",
    "console",
    "PFX_ERROR",
    "PFX_SUCCESS",
    "PFX_INFO",
    "display_validation_error",
    "message_success",
    "message_error",
    "status",
    "storage_panel",
    "error_panel",
    "execution_interrupted_panel",
    "traceback_panel",
    "check_design_configured",
    "md_single_line",
    "load_design_or_exit",
    "build_console",
    "StateSaver",
]

PFX_ERROR = "[icon-error] ✖ [/]"
PFX_SUCCESS = "[icon-success] ✓ [/]"
PFX_WARNING = "[icon-warning] ▲ [/]"
PFX_INFO = "[icon-info] · [/]"


class CLIError(Exception):
    """An error happened while parsing CLI arguments."""


theme = Theme(
    {
        "results": "sky_blue1",
        "progress": "light_slate_blue",
        "storage": "blue",
        "storage-text": "bold",
        "error": "red",
        "explanation": "cyan",
        "spinner": "white on black",
        "icon-success": "green bold on black",
        "icon-error": "red bold on black",
        "icon-info": "blue bold on black",
        "icon-warning": "magenta bold on black",
        "message-success": "white on green",
        "message-error": "white on red",
        "emphasis": "b",
        "e": "b",
        "tree-branch": "b dodger_blue3",
        "partial-cell": "red",
        "unknown-cell": "dodger_blue3",
        "import-rule": "dodger_blue3",
    }
)
"""Theme for the console."""


def build_console(**kwargs) -> Console:
    """Returns a rich console with proper attributes."""
    kwargs = (
        dict(
            theme=theme,
            highlight=False,
            emoji=False,
        )
        | kwargs
    )
    return Console(**kwargs)


console = build_console()
"""Ending's CLI console."""


# Restoration


class StateSaver:
    """Stores and loads a state.

    `StateSaver.save()` saves a namespace and a state object on disk.
    `StateSaver.load()` loads the state object from disk if the namespace matches.
    """

    PATH = ENDING_PATH / "state.pickle"
    """Path to the state data: `~/ending/state.pickle`."""

    @classmethod
    def _clean_ns(cls, ns: Namespace) -> Namespace:
        ns = Namespace(**vars(ns))
        del ns.no_restore
        return ns

    @classmethod
    def load(cls, ns: Namespace) -> State | None:
        """If the namespace matches, returns the state data associated. Otherwise,
        returns `None`.
        """
        if ns.no_restore or not cls.PATH.exists():
            return None

        with cls.PATH.open("rb") as handle:
            data = pickle.load(handle)

        ns = cls._clean_ns(ns)

        if ns == data[0]:
            return data[1]

    @classmethod
    def save(cls, ns: Namespace, state: State) -> None:
        """Saves the state data."""
        ns = cls._clean_ns(ns)
        restore = [ns, state]
        with cls.PATH.open("wb") as handle:
            pickle.dump(restore, handle)

    @classmethod
    def delete(cls) -> None:
        """Deletes the state data."""
        cls.PATH.unlink(missing_ok=True)


# Validation


def md_single_line(message: str, head: str, indent: int = 0) -> Segments:
    return Segments(
        (
            Segment(indent * " "),
            Segment(" └ ", style=console._theme_stack.get("tree-branch")),
        )
        + (
            tuple(console.render(Text.from_markup(head)))[:-1] + (Segment(" "),)
            if head
            else ()
        )
        + tuple(console.render(Markdown(message, inline_code_lexer="python")))
    )


def display_validation_error(error: ValidationError) -> None:
    for problem, solutions in error.problems.items():
        console.print(md_single_line(problem, PFX_ERROR))
        for solution in solutions:
            console.print(md_single_line(solution, None, indent=1))

    cause = error.__cause__ or error.__context__
    if cause:
        try:
            raise cause from None
        except:
            console.print_exception(max_frames=5, suppress=[sys.modules[__name__]])

    message_error("[b]FAILURE")


SPINNERS["ending-cli"] = {
    "interval": 80,
    "frames": [f" {x} " for x in SPINNERS["dots"]["frames"]],
}


def status(msg, **kwargs):
    return console.status(msg, spinner="ending-cli", spinner_style="spinner", **kwargs)


def message_success(message: str) -> None:
    console.print()
    console.print(f"{PFX_SUCCESS}[message-success] {message} [/]")
    console.print()


def message_error(message: str) -> None:
    console.print()
    console.print(f"{PFX_ERROR}[message-error] {message} [/]")
    console.print()


def storage_panel(prefix: str, is_partial: bool) -> None:
    title = is_partial and "Storage [i](partial)[/i]" or "Storage"
    console.print(
        Panel(
            border_style="storage",
            title=title,
            renderable=Align.center(f"[storage-text]{prefix}.[/]"),
        )
    )


def error_panel(message: str) -> None:
    panel = Panel(
        Align.center(message), title="Error", border_style="error", expand=True
    )
    console.print(panel)


def execution_interrupted_panel() -> None:
    panel = Panel(
        Align.center("Execution interrupted [i](run again to resume)[/i]"),
        title="Interrupted",
        border_style="error",
    )
    console.print(panel)


def traceback_panel(trace=None) -> None:
    console.print(Traceback(trace=trace, width=None))


def load_design_or_exit(design_dir: DesignDirectory) -> type[Design] | NoReturn:
    """Loads a design from a directory, properly displaying exceptions if they occur.

    Returns:
        Design: The loaded design.
    """
    try:
        return design_dir.load()
    except DesignLoadException as exception:
        error_panel(str(exception))
        exception = exception.__cause__ or exception.__context__
        trace = Traceback.extract(type(exception), exception, exception.__traceback__)
        traceback_panel(trace)
    except Exception as exception:
        error_panel(
            f"An unexpected error occurred while loading the design: {exception}"
        )
        traceback_panel()

    sys.exit(1)


def check_design_configured(cls: type[Design]) -> None:
    if not cls.is_configured():
        raise CLIError(
            "Design is not configured. Run [b]configure[/] or manually configure it."
        )


class ConsoleLiveStatus(LiveStatus):
    """A live status that displays events nicely in the console. Singleton."""

    __instance: ConsoleLiveStatus

    @classmethod
    def get(cls) -> ConsoleLiveStatus:
        try:
            return cls.__instance
        except AttributeError:
            cls.__instance = cls()
            return cls.__instance

    def __init__(self):
        self._current_section = None
        self._status = None

    def start(self) -> None:
        self._status = status("Working")
        self._status.start()

    def done(self) -> None:
        if self._status:
            self._status.stop()
        self._status = None
        self._current_section = None

    def _display(self, message: str, type: str) -> None:
        console.print(md_single_line(message, type))

    def status(self, message: str) -> None:
        if self._status is None:
            self.start()
        self._status.update(f"{message}…")

    def section(self, name: str, message: str = None) -> None:
        if self._current_section != name:
            self._current_section = name
        console.print(f"[emphasis]{name}[/]")
        if message:
            self.status(message)

    def info(self, message: str) -> None:
        self._display(message, PFX_INFO)

    def success(self, message: str) -> None:
        self._display(message, PFX_SUCCESS)

    def failure(self, message: str) -> None:
        self._display(message, PFX_ERROR)

    def warning(self, message: str) -> None:
        self._display(message, PFX_WARNING)
