"""Pipeline steps (parse -> profile -> graph -> docs) and their background execution."""

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing

from api import llm
from api.appdb import NOW, NotReady, connect, latest_result, load_relationships, read_app_settings
from core.documentation import doc_basis, document_table
from core.parser import parse_database
from core.profiler import profile_database

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


def docs(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    """Document tables with no doc and stale generated ones; edited and approved docs are never touched."""
    db = run["database_id"]
    tables = latest_result(conn, db, "parse")["tables"]
    profiles = {p["table"]: p for p in latest_result(conn, db, "profile")["tables"]}
    app = read_app_settings(conn)
    rels = load_relationships(conn, db)
    existing = {r["table_name"]: r for r in conn.execute(
        "SELECT table_name, status, basis FROM table_docs WHERE database_id = ?", (db,))}
    names = {t["name"] for t in tables}
    conn.executemany("DELETE FROM table_docs WHERE database_id = ? AND table_name = ?",
                     [(db, name) for name in existing if name not in names])
    todo = [t for t in tables if t["name"] not in existing
            or (existing[t["name"]]["status"] == "generated" and existing[t["name"]]["basis"] != doc_basis(t, rels))]
    if any(t["name"] not in profiles for t in todo):  # parse re-run alone after the tables changed
        raise NotReady("run profile first")
    result = {"done": 0, "total": len(todo), "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def progress() -> None:
        conn.execute("UPDATE runs SET result = ? WHERE id = ?", (result, run["run_id"]))
        conn.commit()  # no write transaction stays open while the LLM answers, so relationship edits don't lock

    progress()
    client = llm.client() if todo else None  # nothing to do needs no key
    for table in todo:
        doc, usage = document_table(client, table, profiles[table["name"]], rels,
                                    model=app.llm_model, effort=app.reasoning_effort)
        cost = (usage["input_tokens"] * app.input_usd_per_mtok + usage["output_tokens"] * app.output_usd_per_mtok) / 1e6
        conn.execute(
            "INSERT INTO table_docs (database_id, table_name, summary, columns, status, basis, llm_model,"
            " input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, 'generated', ?, ?, ?, ?, ?)"
            " ON CONFLICT (database_id, table_name) DO UPDATE SET summary = excluded.summary,"
            " columns = excluded.columns, status = excluded.status, basis = excluded.basis,"
            " llm_model = excluded.llm_model, input_tokens = excluded.input_tokens,"
            f" output_tokens = excluded.output_tokens, cost_usd = excluded.cost_usd, updated_at = {NOW}"
            " WHERE table_docs.status = 'generated'",  # a person's PUT during the run wins
            (db, table["name"], doc["summary"], doc["columns"], doc_basis(table, rels), app.llm_model,
             usage["input_tokens"], usage["output_tokens"], cost),
        )
        result["done"] += 1
        result["input_tokens"] += usage["input_tokens"]
        result["output_tokens"] += usage["output_tokens"]
        result["cost_usd"] += cost
        progress()
    return result


STEPS: dict[str, Callable[[sqlite3.Connection, sqlite3.Row], dict]] = {
    "parse": parse, "profile": profile, "graph": graph, "docs": docs,
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
                    "SELECT runs.id AS run_id, step, database_id, path, masked_columns FROM runs"
                    " JOIN databases ON databases.id = runs.database_id WHERE runs.id = ?",
                    (run_id,),
                ).fetchone()
                # The step's writes, marking it done, and starting the next step commit together,
                # so no other pipeline can grab the database between two steps (the docs step also commits per table).
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
