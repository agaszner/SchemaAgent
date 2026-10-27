"""Pages of a table's rows, masked, read straight from the analysed database."""

import sqlite3

from fastapi import APIRouter, HTTPException

from api.appdb import latest_result
from api.deps import Conn, Database
from core.rows import read_rows

router = APIRouter(prefix="/databases/{database_id}", tags=["tables"])


@router.get("/tables/{table}/rows")
def read_table_rows(table: str, db: Database, conn: Conn, offset: int = 0, limit: int = 25, sort: str | None = None,
                    desc: bool = False, column: str | None = None, op: str = "contains",
                    value: str | None = None) -> dict:
    tables = {t["name"]: t for t in latest_result(conn, db["id"], "parse")["tables"]}
    if table not in tables:
        raise HTTPException(404, "table not found")
    marked = {m["column"] for m in db["masked_columns"] if m["table"] == table}
    try:
        return read_rows(db["path"], tables[table], marked, offset=offset, limit=limit, sort=sort,
                         descending=desc, column=column, op=op, value=value)
    except ValueError as err:
        raise HTTPException(400, str(err)) from None
    except sqlite3.Error:
        raise HTTPException(409, "re-run parse: the database changed since the last parse") from None
