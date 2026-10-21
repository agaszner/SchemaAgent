"""Database access. Every read of an analysed database goes through connect_readonly."""

import sqlite3
from pathlib import Path


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite in read-only mode; writes fail at the driver level, not by prompt."""
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
