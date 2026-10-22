"""App state in app.db, kept apart from the analysed databases."""

import json
import sqlite3
from contextlib import closing

import networkx as nx

from api.config import settings
from core.graph import add_relation

# Lists and dicts bind as JSON text, and columns declared JSON read back as lists and dicts
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
sqlite3.register_converter("JSON", json.loads)

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

SCHEMA = """
CREATE TABLE IF NOT EXISTS databases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,                  -- resolved, always inside settings.data_dir
    masked_columns JSON NOT NULL DEFAULT '[]',  -- [{"table", "column"}], masked on top of SENSITIVE_PATTERNS
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    database_id INTEGER NOT NULL REFERENCES databases (id),
    step TEXT NOT NULL,          -- no CHECK: the docs step joins later without a migration
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'error')),
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    result JSON                  -- step output; profile results are cleared when masking changes
);
-- one active pipeline per database: starting a second one violates this and returns 409
CREATE UNIQUE INDEX IF NOT EXISTS one_running_run ON runs (database_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY,
    database_id INTEGER NOT NULL REFERENCES databases (id),
    from_table TEXT NOT NULL,
    from_columns JSON NOT NULL,
    to_table TEXT NOT NULL,
    to_columns JSON NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('declared', 'inferred', 'manual')),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'accepted', 'rejected')),
    confidence REAL NOT NULL DEFAULT 1.0,
    cardinality TEXT CHECK (cardinality IN ('1:1', '1:N', 'N:M')),
    note TEXT,
    -- the graph step's ON CONFLICT DO NOTHING targets this, so decisions and manual edges survive re-runs
    UNIQUE (database_id, from_table, from_columns, to_table, to_columns)
);

CREATE TABLE IF NOT EXISTS relationship_log (
    id INTEGER PRIMARY KEY,
    database_id INTEGER NOT NULL REFERENCES databases (id),
    relationship_id INTEGER NOT NULL,   -- no FK: entries outlive deleted manual relationships
    action TEXT NOT NULL,               -- create | status | delete
    detail TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class NotReady(Exception):
    """A step's input does not exist yet; main.py answers 409."""


def connect() -> sqlite3.Connection:
    # check_same_thread=False: a sync yield-dependency may enter and exit on different threadpool threads
    conn = sqlite3.connect(settings.app_db, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    # No WAL: one user and one background writer don't need it, and its -wal/-shm files are not gitignored
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
        # Background tasks die with the server; their runs would otherwise block the database forever
        conn.execute(
            "UPDATE runs SET status = 'error', error = 'interrupted by server restart'"
            " WHERE status IN ('queued', 'running')"
        )


def latest_result(conn: sqlite3.Connection, database_id: int, step: str) -> dict:
    """Output of the step's newest finished run."""
    row = conn.execute(
        "SELECT result FROM runs WHERE database_id = ? AND step = ? AND status = 'done' AND result IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (database_id, step),
    ).fetchone()
    if row is None:
        raise NotReady(f"run {step} first")
    return row["result"]


def load_graph(conn: sqlite3.Connection, database_id: int) -> nx.MultiDiGraph:
    """The reviewed graph: the parsed tables, and every stored relationship as an edge with its current status."""
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(t["name"] for t in latest_result(conn, database_id, "parse")["tables"])
    latest_result(conn, database_id, "graph")  # raises NotReady: an unbuilt graph would answer "not connected"
    # Not build_graph: it would add the declared FKs as accepted again, undoing a disable.
    # ORDER BY id: find_path keeps the first of equal-weight parallel edges.
    for row in conn.execute("SELECT * FROM relationships WHERE database_id = ? ORDER BY id", (database_id,)):
        rel = {"table": row["from_table"], "columns": row["from_columns"], "ref_table": row["to_table"],
               "ref_columns": row["to_columns"], "confidence": row["confidence"]}
        add_relation(graph, rel, source=row["source"], status=row["status"])
    return graph
