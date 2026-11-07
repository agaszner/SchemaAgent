"""Guarded SQL against an analysed database."""

from fastapi import APIRouter, HTTPException

from api.appdb import latest_result, read_app_settings
from api.deps import Conn, Database
from api.payloads import SqlIn
from core.sql_guard import GuardError, run_sql

router = APIRouter(prefix="/databases/{database_id}", tags=["sql"])


@router.post("/sql")
def run_query(body: SqlIn, db: Database, conn: Conn) -> dict:
    model = latest_result(conn, db["id"], "parse")
    limits = read_app_settings(conn)
    marks = [(m["table"], m["column"]) for m in db["masked_columns"]]
    try:
        return run_sql(db["path"], body.sql, model, marks, max_rows=limits.max_rows, timeout_s=limits.timeout_s)
    except GuardError as err:
        raise HTTPException(400, str(err)) from None
