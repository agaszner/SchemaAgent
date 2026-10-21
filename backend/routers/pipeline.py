"""Pipeline runs of a database, and the model and profile they produced."""

import sqlite3

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.dependencies import Conn, Database
from backend.runner import create_runs, execute
from backend.schemas import RunIn, RunOut
from backend.storage import latest_result

router = APIRouter(prefix="/databases/{database_id}", tags=["pipeline"])

# duration_s stays NULL until a run has both timestamps
# has_result is false for a done profile run whose output masking cleared
SELECT_RUNS = (
    "SELECT id, step, status, started_at, finished_at, error, result IS NOT NULL AS has_result,"
    " (julianday(finished_at) - julianday(started_at)) * 86400 AS duration_s FROM runs"
)


@router.post("/runs", status_code=202, response_model=list[RunOut])
def start_runs(body: RunIn, db: Database, conn: Conn, background_tasks: BackgroundTasks) -> list[dict]:
    try:
        ids = create_runs(conn, db["id"], body.step)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "a pipeline run is already in progress") from None
    background_tasks.add_task(execute, ids)
    return [dict(conn.execute(f"{SELECT_RUNS} WHERE id = ?", (run_id,)).fetchone()) for run_id in ids]


@router.get("/runs", response_model=list[RunOut])
def list_runs(db: Database, conn: Conn) -> list[dict]:
    rows = conn.execute(f"{SELECT_RUNS} WHERE database_id = ? ORDER BY id DESC", (db["id"],))  # newest first
    return [dict(row) for row in rows]


@router.get("/model")
def read_model(db: Database, conn: Conn) -> dict:
    return latest_result(conn, db["id"], "parse")


@router.get("/profile")
def read_profile(db: Database, conn: Conn) -> dict:
    return latest_result(conn, db["id"], "profile")
