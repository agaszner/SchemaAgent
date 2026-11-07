"""The benchmark (spec §7): every question under configs A, B and C as one background run."""

import sqlite3

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.appdb import NotReady, latest_result
from api.deps import Conn, Database
from api.jobs import _start, benchmark_questions, execute
from api.payloads import BenchmarkOut, RunOut
from api.routes.pipeline import SELECT_RUNS

router = APIRouter(prefix="/databases/{database_id}/benchmark", tags=["benchmark"])


@router.post("", status_code=202, response_model=RunOut)
def start_benchmark(db: Database, conn: Conn, background_tasks: BackgroundTasks) -> dict:
    for step in ("parse", "profile", "graph"):
        latest_result(conn, db["id"], step)  # raises NotReady
    tables = {t["name"] for t in latest_result(conn, db["id"], "parse")["tables"]}
    documented = {r["table_name"] for r in conn.execute(
        "SELECT table_name FROM table_docs WHERE database_id = ?", (db["id"],))}
    if tables - documented:
        raise NotReady("run docs first")
    benchmark_questions(db["name"])  # raises NotReady
    run_id = conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'benchmark', 'queued')",
                          (db["id"],)).lastrowid
    try:
        _start(conn, run_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "a pipeline run is already in progress") from None
    background_tasks.add_task(execute, [run_id])
    return dict(conn.execute(f"{SELECT_RUNS} WHERE id = ?", (run_id,)).fetchone())


@router.get("", response_model=BenchmarkOut | None)
def read_benchmark(db: Database, conn: Conn) -> dict | None:
    run = conn.execute(f"{SELECT_RUNS} WHERE database_id = ? AND step = 'benchmark' ORDER BY id DESC LIMIT 1",
                       (db["id"],)).fetchone()
    if run is None:
        return None
    result = conn.execute("SELECT result FROM runs WHERE id = ?", (run["id"],)).fetchone()["result"] or {}
    return {"run": dict(run), "summary": result.get("summary"), "rows": result.get("rows", [])}
