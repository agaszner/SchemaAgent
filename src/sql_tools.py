"""Database access. Every read of an analysed database goes through connect_readonly."""

import sqlite3
from pathlib import Path


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite in read-only mode; writes fail at the driver level, not by prompt."""
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def affinity(declared_type: str | None) -> str:
    """Column affinity SQLite derives from a declared type, checking its rules in order.

    https://www.sqlite.org/datatype3.html#determination_of_column_affinity
    """
    t = (declared_type or "").upper()  # SQLite matches case-insensitively; PRAGMA types can be lowercase
    if "INT" in t:
        return "INTEGER"
    if any(s in t for s in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if any(s in t for s in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"
