"""SQL quoting functions.

Quoting functions converts `str` into a valid SQL string representation.

Examples:

    >>> print(quoting.singlequote("ABC'DEF"))
    'ABC''DEF'
    >>> print(quoting.concat_char("ABC"))
    CONCAT(CHAR(65),CHAR(66),CHAR(67))
"""

from __future__ import annotations

import functools
import typing

__all__ = [
    "singlequote",
    "doublequote",
    "singlequote_backslash",
    "doublequote_backslash",
    "hexadecimal",
    "xstring",
    "concat_char",
    "concat_chr",
    "pipes_char",
    "pipes_chr",
    "sum_char",
    "sum_chr",
    "char",
]


def wrap_join_format(
    wrapper: str = "{}",
    formatter: str = "{}",
    jointure: str = ",",
    empty: str = None,
    transform=ord,
    wrap_single: bool = False,
):
    """Generates a function that takes one parameter, `data`.
    For every char in `data`, it will format them using `format`, join them
    using `jointure`, and wrap the whole thing using `wrapper`.
    If the given data is empty, and empty is set, the latter is returned
    instead.

    Examples:
        >>> wrap_join_format(b'ABC')
        '65,66,67'
        >>> wrap_join_format(formatter='{:c}', jointure='')(b'ABC')
        'ABC'
        >>> wrap_join_format('CONCAT({})', 'CHR(0x{:02x})')('ABC')
        'CONCAT(CHR(0x41),CHR(0x42),CHR(0x43))'
        >>> wrap_join_format(empty='something_else')('')
        'something_else'
    """

    def decorator(function: typing.Callable):
        @functools.wraps(function)
        def function(data: str) -> str:
            match len(data):
                case 0 if empty is not None:
                    return empty
                case 1 if not wrap_single:
                    return formatter.format(transform(data[0]))
                case _:
                    return wrapper.format(
                        jointure.join(formatter.format(transform(b)) for b in data)
                    )

        return function

    return decorator


def singlequote(data: str) -> str:
    """`ABC'DEF` -> `'ABC''DEF'`"""
    return "'" + data.replace("'", "''") + "'"


def doublequote(data: str) -> str:
    """`ABC"DEF` -> `"ABC""DEF"`"""
    return '"' + data.replace('"', '""') + '"'


def hexadecimal(data: str) -> str:
    """`ABC` -> `0x414243`"""
    if not data:
        return "TRIM(0x20)"
    return f"0x{data.encode().hex()}"


def singlequote_backslash(data: str) -> str:
    """Encloses string into single quotes. Escapes relevant characters with a
    backslash. These characters are:

    * `'`
    * `\\`
    * `\\n`
    * `\\t`
    * `\\r`
    * `\\0`
    """
    t = {
        "\\": "\\\\",
        "'": "\\'",
        "\n": "\\n",
        "\t": "\\t",
        "\r": "\\r",
        "\0": "\\0",
    }
    data = "".join(t.get(c, c) for c in data)
    return f"'{data}'"


def doublequote_backslash(data: str) -> str:
    """Encloses string into double quotes. Escapes relevant characters with a
    backslash. These characters are:

    * `'`
    * `\\`
    * `\\n`
    * `\\t`
    * `\\r`
    * `\\0`
    """
    t = {
        "\\": "\\\\",
        '"': '\\"',
        "\n": "\\n",
        "\t": "\\t",
        "\r": "\\r",
        "\0": "\\0",
    }
    data = "".join(t.get(c, c) for c in data)
    return f'"{data}"'


def xstring(data: str) -> str:
    """`ABC` -> `X'414243'`"""
    return f"X'{data.encode().hex()}'"


@wrap_join_format(formatter="CHR({})", jointure="+", empty="TRIM(CHR(32))")
def sum_chr(data: str) -> str:
    """`'ABC'` -> `'CHR(65)+CHR(66)+CHR(67)'`"""


@wrap_join_format(formatter="CHAR({})", jointure="+", empty="TRIM(CHAR(32))")
def sum_char(data: str) -> str:
    """`'ABC'` -> `'CHAR(65)+CHAR(66)+CHAR(67)'`"""


@wrap_join_format(formatter="CHR({})", jointure="|", empty="TRIM(CHR(32))")
def pipes_chr(data: str) -> str:
    """`'ABC'` -> `'CHR(65)|CHR(66)|CHR(67)'`"""


@wrap_join_format(formatter="CHAR({})", jointure="|", empty="TRIM(CHAR(32))")
def pipes_char(data: str) -> str:
    """`'ABC'` -> `'CHAR(65)|CHAR(66)|CHAR(67)'`"""


@wrap_join_format(formatter="CHAR({})", jointure="||", empty="TRIM(CHAR(32))")
def dpipes_char(data: str) -> str:
    """`'ABC'` -> `'CHAR(65)||CHAR(66)||CHAR(67)'`"""


@wrap_join_format(formatter="CHR({})", jointure="||", empty="TRIM(CHR(32))")
def dpipes_chr(data: str) -> str:
    """`'ABC'` -> `'CHR(65)||CHR(66)||CHR(67)'`"""


@wrap_join_format(
    wrapper="CONCAT({})", formatter="CHR({})", jointure=",", empty="TRIM(CHR(32))"
)
def concat_chr(data: str) -> str:
    """`'ABC'` -> `'CONCAT(CHR(65),CHR(66),CHR(67))'`"""


@wrap_join_format(
    wrapper="CONCAT({})", formatter="CHAR({})", jointure=",", empty="TRIM(CHAR(32))"
)
def concat_char(data: str) -> str:
    """`'ABC'` -> `'CONCAT(CHAR(65),CHAR(66),CHAR(67))'`"""


@wrap_join_format(
    wrapper="CHAR({})", formatter="{}", jointure=",", empty="CHAR()", wrap_single=True
)
def char(data: str) -> str:
    """`'ABC'` -> `'CHAR(65,66,67)'`"""
