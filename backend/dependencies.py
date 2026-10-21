"""Annotated dependencies shared by the routers."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from backend.storage import connect


def get_conn() -> Iterator[sqlite3.Connection]:
    with closing(connect()) as conn, conn:  # commit on success, rollback on error, then close
        yield conn


# scope="function" commits before the response is sent, so background tasks and follow-up requests see the writes
Conn = Annotated[sqlite3.Connection, Depends(get_conn, scope="function")]


def get_database(database_id: int, conn: Conn) -> sqlite3.Row:
    db = conn.execute("SELECT * FROM databases WHERE id = ?", (database_id,)).fetchone()
    if db is None:
        raise HTTPException(404, "database not found")
    return db


Database = Annotated[sqlite3.Row, Depends(get_database)]

# No auth: the X-Actor header names who made a change in the audit log; routes default it to "local"
Actor = Annotated[str, Header(alias="X-Actor", min_length=1)]
