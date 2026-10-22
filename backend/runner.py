"""Pipeline steps (parse -> profile -> graph) and their background execution."""

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing

from backend.storage import NOW, connect, latest_result
from src.parser import parse_database
from src.profiler import profile_database

log = logging.getLogger(__name__)


def parse(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    return parse_database(run["path"])


def profile(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    return profile_database(
        run["path"],
        model=latest_result(conn, run["database_id"], "parse"),  # the same model the graph step reads
        # tuples: a {"table", "column"} dict would unpack into its key names and mask nothing
        masked_columns=[(m["table"], m["column"]) for m in run["masked_columns"]],
    )


def graph(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    """Seed relationships with the declared FKs and the profile's FK candidates."""
    model = latest_result(conn, run["database_id"], "parse")
    candidates = latest_result(conn, run["database_id"], "profile")["fk_candidates"]
    declared = [{"table": t["name"], **fk, "source": "declared", "status": "accepted", "confidence": 1.0}
                for t in model["tables"] for fk in t["foreign_keys"]]
    inferred = [{**c, "source": "inferred", "status": "proposed"} for c in candidates]
    # Drop untouched suggestions the profile no longer makes (e.g. after a finder change); reviewed rows stay
    fresh = {(c["table"], tuple(c["columns"]), c["ref_table"], tuple(c["ref_columns"])) for c in candidates}
    stale = [(row["id"],) for row in conn.execute(
        "SELECT id, from_table, from_columns, to_table, to_columns FROM relationships"
        " WHERE database_id = ? AND source = 'inferred' AND status = 'proposed'", (run["database_id"],))
        if (row["from_table"], tuple(row["from_columns"]), row["to_table"], tuple(row["to_columns"])) not in fresh]
    conn.executemany("DELETE FROM relationships WHERE id = ?", stale)
    # ON CONFLICT leaves a stored pair alone, so review decisions and manual edges survive re-runs,
    # but refreshes an untouched suggestion's confidence; unlike OR IGNORE, a row breaking CHECK or
    # NOT NULL still raises and fails the step
    conn.executemany(
        "INSERT INTO relationships"
        " (database_id, from_table, from_columns, to_table, to_columns, source, status, confidence)"
        " VALUES (:database_id, :table, :columns, :ref_table, :ref_columns, :source, :status, :confidence)"
        " ON CONFLICT (database_id, from_table, from_columns, to_table, to_columns) DO UPDATE"
        " SET confidence = excluded.confidence"
        " WHERE relationships.source = 'inferred' AND relationships.status = 'proposed'",
        [{**rel, "database_id": run["database_id"]} for rel in declared + inferred],
    )
    return {"declared": len(declared), "inferred": len(inferred)}  # found, not inserted: re-runs insert nothing


STEPS: dict[str, Callable[[sqlite3.Connection, sqlite3.Row], dict]] = {
    "parse": parse, "profile": profile, "graph": graph,
}


def _start(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(f"UPDATE runs SET status = 'running', started_at = {NOW} WHERE id = ?", (run_id,))


def create_runs(conn: sqlite3.Connection, database_id: int, step: str) -> list[int]:
    """Queue the step, or every step for "all"; the first one is running at once."""
    order = list(STEPS)
    if step == "all":
        steps = order
    else:
        steps = [step]
        for prerequisite in order[:order.index(step)]:
            latest_result(conn, database_id, prerequisite)  # raises NotReady
    insert = "INSERT INTO runs (database_id, step, status) VALUES (?, ?, 'queued')"
    ids = [conn.execute(insert, (database_id, s)).lastrowid for s in steps]
    _start(conn, ids[0])  # IntegrityError on one_running_run while another pipeline is active
    return ids


def execute(run_ids: list[int]) -> None:
    """Background task; it opens its own connection, because the request's one is closed by then."""
    with closing(connect()) as conn:
        for i, run_id in enumerate(run_ids):
            try:
                run = conn.execute(
                    "SELECT step, database_id, path, masked_columns FROM runs"
                    " JOIN databases ON databases.id = runs.database_id WHERE runs.id = ?",
                    (run_id,),
                ).fetchone()
                # The step's writes, marking it done, and starting the next step commit together,
                # so no other pipeline can grab the database between two steps.
                with conn:
                    result = STEPS[run["step"]](conn, run)
                    conn.execute(f"UPDATE runs SET status = 'done', result = ?, finished_at = {NOW} WHERE id = ?",
                                 (result, run_id))
                    if i + 1 < len(run_ids):
                        _start(conn, run_ids[i + 1])  # after the done update: the index is checked per statement
            except Exception as e:
                log.exception("run %s failed", run_id)
                with conn:
                    step = conn.execute(  # RETURNING: the run row itself may be what failed to load
                        f"UPDATE runs SET status = 'error', error = ?, finished_at = {NOW}"
                        " WHERE id = ? RETURNING step",
                        (f"{type(e).__name__}: {e}", run_id),
                    ).fetchone()["step"]
                    conn.executemany("UPDATE runs SET status = 'error', error = ? WHERE id = ?",
                                     [(f"skipped: {step} failed", later) for later in run_ids[i + 1:]])
                return
