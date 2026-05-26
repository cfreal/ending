"""Generic code.

Holds base code for compiler and SQL injection methods.
"""

from ending.db.generic.compiler import *
from ending.db.generic.fetcher import *
from ending.db.generic.map import *
from ending.db.generic.method import *
from ending.util.typing import QuoteCallable


class Features:
    """A list of defining elements of the DBMS."""

    name: str = "Generic DBMS"
    """Name of the DBMS."""
    quoters: list[QuoteCallable] = ...
    """List of quoting functions available for the DBMS."""
    tautology: str = ...
    """A DBMS-specific tautology. The expression should always return true when ran
    against the database, and produce an error on other databases.
    """
    error_condition: str = ...
    """A format string that produces an SQL expression that results in an error when
    given condition is true, but not when it is false.
    The format string bears a `{payload}` and a `{result}` format.
    """

    def __init__(self):
        raise RuntimeError("Features class cannot be instanciated")
