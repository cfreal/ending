"""Everything related to logging."""

import logging
import os.path
from logging import Logger

from rich.console import Console
from rich.logging import RichHandler

__all__ = [
    "logger",
    "set_level",
    "CRITICAL",
    "ERROR",
    "WARNING",
    "INFO",
    "DEBUG",
    "NOTSET",
    "SQL",
]


def _define_log_level(levelName, levelNum, methodName=None):
    """Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    Source: https://stackoverflow.com/questions/2183233/how-to-add-a-custom-loglevel-to-pythons-logging-facility/35804945#35804945
    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        raise AttributeError(f"{levelName} already defined in logging module")
    if hasattr(logging, methodName):
        raise AttributeError(f"{methodName} already defined in logging module")
    if hasattr(logging.getLoggerClass(), methodName):
        raise AttributeError(f"{methodName} already defined in logger class")

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


def logger(name) -> Logger:
    """Return a logger with the specified name or, if name is None, return a
    logger which is the root logger of the hierarchy.
    """
    return logging.getLogger(name)


def set_level(level):
    """Sets the threshold for the file logger to `level`."""
    logger(None).setLevel(level)


def _create_default_file_handler() -> RichHandler:
    base = os.path.expanduser("~/ending")
    try:
        os.mkdir(base, mode=0o700)
    except FileExistsError:
        pass
    file = open(base + "/ending.log", "a+")
    console = Console(
        file=file,
        emoji=False,
        highlight=False,
        color_system="truecolor",
        width=LOG_LINE_WIDTH,
    )
    return RichHandler(console=console)


LOG_LINE_WIDTH = 200
"""Maximum length for a log line."""

# Create logging levels

_define_log_level("SQL", 9)

# Reference logging levels

# Original
CRITICAL = logging.CRITICAL
ERROR = logging.ERROR
WARNING = logging.WARNING
INFO = logging.INFO
DEBUG = logging.DEBUG
NOTSET = logging.NOTSET

# Added
SQL = logging.SQL

# Kill unneeded logging
# TODO should it stay here ?
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("rich").setLevel(logging.ERROR)

# Add our default handler

logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[_create_default_file_handler()],
)
