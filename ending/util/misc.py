"""Some utility functions and variables."""

import os.path
from pathlib import Path
from typing import Iterable, Iterator, Union

__all__ = [
    "ENDING_PATH",
    "repr_attrs",
    "niter",
    "to_bytes",
    "SQL_KEYWORDS",
]

ENDING_PATH = Path(os.path.expanduser("~/ending"))
"""Path to the main directory."""


def repr_attrs(obj, attrs):
    """Returns a string representation of the object, with its name and selected
    attributes.
    """

    cls = type(obj).__name__
    attrs = [(k, getattr(obj, k)) for k in attrs]
    attrs = ", ".join(f"{k}={v!r}" for k, v in attrs)
    return f"{cls}({attrs})"


def niter(data: Iterable, n: int) -> Iterator[tuple]:
    """Yields `n` items for an iterable, then the next `n` items, etc.

    >>> for part in niter(range(10), 3)
    ...  print(part)
    (0, 1, 2)
    (3, 4, 5)
    (6, 7, 8)
    (9, )
    """
    if n <= 0:
        raise ValueError(f"niter(): n needs to be strictly positive: {n!r}")

    if isinstance(data, bytes):
        cast = bytes
    elif isinstance(data, str):
        cast = "".join
    else:
        cast = lambda x: tuple(x)

    got = []
    ngot = 0

    for item in iter(data):
        ngot += 1
        got.append(item)

        if ngot == n:
            ngot = 0
            yield cast(got)
            got = []

    if ngot:
        yield cast(got)


def to_bytes(value: str | int | float | bytes, none: bytes = None) -> bytes:
    """Converts value to bytes. If `none` is specified and `value` is None, returns the
    former. Otherwise, returns None.
    """

    match value:
        case int() | float():
            return str(value).encode()
        case str():
            return value.encode()
        case None:
            return none
        case bytes():
            return value
        case memoryview():
            return value.tobytes()
        case _:
            raise TypeError(f"Cannot cast to bytes: {value!r}")


SQL_KEYWORDS = {
    "ABORT",
    "ACTION",
    "ADD",
    "AFTER",
    "ALL",
    "ALTER",
    "ANALYZE",
    "AND",
    "ANY",
    "APPLY",
    "AS",
    "ASC",
    "ATTACH",
    "AUTOINCREMENT",
    "AVG",
    "BACKUP",
    "BEFORE",
    "BEGIN",
    "BETWEEN",
    "BIGINT",
    "BIGSERIAL",
    "BINARY",
    "BIT",
    "BY",
    "CASCADE",
    "CASE",
    "CAST",
    "CHANGE",
    "CHAR",
    "CHARSET",
    "CHECK",
    "CITEXT",
    "CLUSTER",
    "COALESCE",
    "COLLATE",
    "COLUMN",
    "COMMIT",
    "CONFLICT",
    "CONNECTION",
    "CONNECT_BY",
    "CONSTRAINT",
    "CONTAINS",
    "CONTAINSTABLE",
    "COUNT",
    "CREATE",
    "CROSS",
    "CURRENT_DATE",
    "CURRENT_TIME",
    "CURRENT_TIMESTAMP",
    "CURRENT_USER",
    "DATABASE",
    "DATABASES",
    "DATE",
    "DAY",
    "DECIMAL",
    "DECLARE",
    "DEFAULT",
    "DEFERRABLE",
    "DEFERRED",
    "DELAYED",
    "DELETE",
    "DESC",
    "DESCRIBE",
    "DETACH",
    "DISTINCT",
    "DIV",
    "DOUBLE",
    "DROP",
    "DUAL",
    "EACH",
    "ELSE",
    "END",
    "ENGINE",
    "EXCEPT",
    "EXCLUSIVE",
    "EXEC",
    "EXISTS",
    "EXPLAIN",
    "EXTERNAL",
    "FAIL",
    "FALSE",
    "FETCH",
    "FIELDS",
    "FILTER",
    "FIRST",
    "FLOAT",
    "FOLLOWING",
    "FOR",
    "FOREIGN",
    "FREETEXT",
    "FREETEXTTABLE",
    "FROM",
    "FULL",
    "FULLTEXT",
    "GLOB",
    "GRANT",
    "GROUP",
    "GROUP BY",
    "HAVING",
    "HIGH_PRIORITY",
    "HSTORE",
    "IF",
    "IF EXISTS",
    "IF NOT EXISTS",
    "IFNULL",
    "IGNORE",
    "ILIKE",
    "IMMEDIATE",
    "IN",
    "INDEX",
    "INFILE",
    "INITIALLY",
    "INNER",
    "INSERT",
    "INSERT INTO",
    "INSTEAD",
    "INT",
    "INTEGER",
    "INTERSECT",
    "INTERVAL",
    "INTO",
    "IS",
    "ISOLATION",
    "JOIN",
    "JSON",
    "JSONB",
    "KEY",
    "KILL",
    "LAST",
    "LATERAL",
    "LEADING",
    "LEFT",
    "LEVEL",
    "LIKE",
    "LIMIT",
    "LINE",
    "LINES",
    "LOCALTIME",
    "LOCALTIMESTAMP",
    "LOCK",
    "LONG",
    "MASTER",
    "MATCH",
    "MAX",
    "MERGE",
    "MIN",
    "MODEL",
    "MODIFY",
    "NATIONAL",
    "NATURAL",
    "NCHAR",
    "NO",
    "NOCYCLE",
    "NOT",
    "NULL",
    "NUMERIC",
    "OF",
    "OFFSET",
    "ON",
    "ONLY",
    "OPTIMIZE",
    "OPTION",
    "OR",
    "ORDER",
    "OTHERS",
    "OUTER",
    "OVER",
    "PARTITION",
    "PIVOT",
    "PLAN",
    "PRAGMA",
    "PRECEDING",
    "PRIMARY",
    "PRIOR",
    "PROCEDURE",
    "PROCESSLIST",
    "PUBLIC",
    "QUERY",
    "RAISE",
    "RANGE",
    "RECURSIVE",
    "REFERENCES",
    "REGEXP",
    "REINDEX",
    "RELEASE",
    "RENAME",
    "REPLACE",
    "RESTRICT",
    "RETURN",
    "RETURNING",
    "REVOKE",
    "RIGHT",
    "ROLLBACK",
    "ROW",
    "ROWID",
    "ROWNUM",
    "ROW_NUMBER",
    "SAVEPOINT",
    "SCHEMA",
    "SCHEMAS",
    "SELECT",
    "SEQUENCE",
    "SERIAL",
    "SESSION",
    "SET",
    "SHOW",
    "SIMILAR",
    "SMALLINT",
    "SMALLSERIAL",
    "SOME",
    "SQL",
    "SQL_CALC_FOUND_ROWS",
    "START",
    "START_WITH",
    "STORAGE",
    "STRAIGHT_JOIN",
    "SUBSTRING",
    "SUM",
    "SYSDATETIME",
    "SYS_CONNECT_BY_PATH",
    "TABLE",
    "TABLESAMPLE",
    "TEMP",
    "TEMPORARY",
    "THEN",
    "TIES",
    "TINYBLOB",
    "TINYINT",
    "TINYTEXT",
    "TO",
    "TOP",
    "TRANSACTION",
    "TRIGGER",
    "TRUE",
    "TRUNCATE",
    "TRY_CAST",
    "TRY_CONVERT",
    "TRY_PARSE",
    "TSQUERY",
    "TSVECTOR",
    "UID",
    "UNBOUNDED",
    "UNION",
    "UNIQUE",
    "UNLOCK",
    "UNLOGGED",
    "UNPIVOT",
    "UNSIGNED",
    "UPDATE",
    "USAGE",
    "USE",
    "USERENV",
    "USING",
    "VACUUM",
    "VALUES",
    "VARCHAR",
    "VARRAY",
    "VIEW",
    "VIRTUAL",
    "WHEN",
    "WHERE",
    "WHILE",
    "WINDOW",
    "WITH",
    "WITH RECURSIVE",
    "WITHIN GROUP",
    "WITHOUT",
    "WITHOUT ROWID",
    "WORK",
    "WRITE",
    "XML",
    "XMLDATA",
    "XMLSCHEMA",
    "XMLTABLE",
    "XOR",
    "YEAR",
    "ZEROFILL",
    "ZONE",
}
"""SQL keywords"""
