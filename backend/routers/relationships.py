"""Relationship review (filter, add, accept or reject, delete or disable, audit log) and the JOIN path over it."""

import sqlite3
from contextlib import closing
from typing import Literal

import networkx as nx
from fastapi import APIRouter, HTTPException

from backend.dependencies import Actor, Conn, Database
from backend.schemas import (
    BulkAcceptIn, LogEntry, PathStep, RelationshipCheck, RelationshipIn, RelationshipOut, StatusIn,
)
from backend.storage import latest_result, load_graph
from src.profiler import value_coverage
from src.schema_graph import find_path
from src.sql_tools import affinity, connect_readonly

router = APIRouter(tags=["relationships"])

LOW_COVERAGE = 0.9  # /check warns when a smaller share of the from-values exists in the target


def _get_relationship(conn: sqlite3.Connection, relationship_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM relationships WHERE id = ?", (relationship_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "relationship not found")
    return row


def _describe(rel: sqlite3.Row) -> str:
    """Log detail naming the edge, e.g. "Invoice(CustomerId) -> Customer(CustomerId)"."""
    return (f"{rel['from_table']}({', '.join(rel['from_columns'])})"
            f" -> {rel['to_table']}({', '.join(rel['to_columns'])})")


def _log(conn: sqlite3.Connection, relationship: sqlite3.Row, action: str, detail: str, actor: str) -> None:
    conn.execute(
        "INSERT INTO relationship_log (database_id, relationship_id, action, detail, actor) VALUES (?, ?, ?, ?, ?)",
        (relationship["database_id"], relationship["id"], action, detail, actor),
    )


def _column_types(conn: sqlite3.Connection, database_id: int,
                  rel: RelationshipIn) -> tuple[list[str | None], list[str | None]]:
    """Declared types of both sides from the parsed model; 422 for a name the model does not hold.

    Exact names: value_coverage quotes them, and SQLite reads an unknown double-quoted name as a string literal.
    """
    model = {t["name"]: {c["name"]: c["type"] for c in t["columns"]}
             for t in latest_result(conn, database_id, "parse")["tables"]}

    def types(table: str, columns: list[str]) -> list[str | None]:
        if table not in model:
            raise HTTPException(422, f"unknown table {table}")
        for column in columns:
            if column not in model[table]:
                raise HTTPException(422, f"unknown column {table}.{column}")
        return [model[table][column] for column in columns]

    return types(rel.from_table, rel.from_columns), types(rel.to_table, rel.to_columns)


@router.get("/databases/{database_id}/relationships", response_model=list[RelationshipOut])
def list_relationships(
    db: Database,
    conn: Conn,
    source: Literal["declared", "inferred", "manual"] | None = None,
    status: Literal["proposed", "accepted", "rejected"] | None = None,
    min_confidence: float = 0,
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM relationships WHERE database_id = :database_id"
        " AND (:source IS NULL OR source = :source) AND (:status IS NULL OR status = :status)"
        " AND confidence >= :min_confidence ORDER BY confidence DESC, id",
        {"database_id": db["id"], "source": source, "status": status, "min_confidence": min_confidence},
    )
    return [dict(row) for row in rows]


@router.post("/databases/{database_id}/relationships/check", response_model=RelationshipCheck)
def check_relationship(body: RelationshipIn, db: Database, conn: Conn) -> dict:
    """Value coverage, declared types and warnings for a relationship before it is saved; writes nothing."""
    from_types, to_types = _column_types(conn, db["id"], body)  # before any query sees the names
    with closing(connect_readonly(db["path"])) as analysed:
        coverage = value_coverage(analysed, body.from_table, body.from_columns, body.to_table, body.to_columns)
    warnings = []
    if coverage < LOW_COVERAGE:
        warnings.append(f"only {coverage:.0%} of {body.from_table}.{'/'.join(body.from_columns)}"
                        f" values exist in {body.to_table}.{'/'.join(body.to_columns)}")
    for from_column, from_type, to_column, to_type in zip(body.from_columns, from_types, body.to_columns, to_types):
        a, b = affinity(from_type), affinity(to_type)
        if a != b and not {a, b} <= {"INTEGER", "REAL", "NUMERIC"}:  # 1 = 1.0: numbers compare by value
            warnings.append(f"type mismatch: {body.from_table}.{from_column} {from_type}"
                            f" vs {body.to_table}.{to_column} {to_type}")
    return {"coverage": coverage, "from_types": from_types, "to_types": to_types, "warnings": warnings}


@router.post("/databases/{database_id}/relationships", status_code=201, response_model=RelationshipOut)
def create_relationship(body: RelationshipIn, db: Database, conn: Conn, actor: Actor = "local") -> dict:
    """Store a hand-made relationship as accepted; /check only warns, because the user decides."""
    _column_types(conn, db["id"], body)
    params = {"database_id": db["id"], **body.model_dump()}  # lists bind as the same JSON the UNIQUE key compares
    try:
        row = conn.execute(
            "INSERT INTO relationships (database_id, from_table, from_columns, to_table, to_columns,"
            " source, status, confidence, cardinality, note)"
            " VALUES (:database_id, :from_table, :from_columns, :to_table, :to_columns,"
            " 'manual', 'accepted', 1.0, :cardinality, :note) RETURNING *",
            params,
        ).fetchone()
    except sqlite3.IntegrityError:  # the UNIQUE pair: every other constrained value is fixed or validated
        # The failed INSERT keeps its write lock, so the stored row cannot change before this read
        existing = conn.execute(
            "SELECT id, status FROM relationships WHERE database_id = :database_id AND from_table = :from_table"
            " AND from_columns = :from_columns AND to_table = :to_table AND to_columns = :to_columns",
            params,
        ).fetchone()
        raise HTTPException(409, f"relationship already exists (id {existing['id']}, {existing['status']})") from None
    _log(conn, row, "create", _describe(row), actor)
    return dict(row)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipOut)
def set_status(relationship_id: int, body: StatusIn, conn: Conn, actor: Actor = "local") -> dict:
    # Write lock before the read: a concurrent PATCH or DELETE waits for this commit, so the logged old status holds
    conn.execute("BEGIN IMMEDIATE")
    rel = _get_relationship(conn, relationship_id)
    _log(conn, rel, "status", f"{rel['status']} -> {body.status}", actor)
    row = conn.execute(
        "UPDATE relationships SET status = ? WHERE id = ? RETURNING *", (body.status, relationship_id)
    ).fetchone()
    return dict(row)


@router.delete("/relationships/{relationship_id}", status_code=204)
def delete_relationship(relationship_id: int, conn: Conn, actor: Actor = "local") -> None:
    # Write lock before the read: a concurrent PATCH or DELETE waits for this commit, so the row is logged once
    conn.execute("BEGIN IMMEDIATE")
    rel = _get_relationship(conn, relationship_id)
    if rel["source"] == "declared":
        raise HTTPException(409, "declared foreign keys come from the schema; set status to rejected to disable them")
    _log(conn, rel, "delete", _describe(rel), actor)
    if rel["source"] == "manual":
        conn.execute("DELETE FROM relationships WHERE id = ?", (relationship_id,))
    else:  # inferred: the rejected row stops the graph step from proposing the pair again
        conn.execute("UPDATE relationships SET status = 'rejected' WHERE id = ?", (relationship_id,))


@router.post("/databases/{database_id}/relationships/accept", response_model=list[RelationshipOut])
def accept_relationships(body: BulkAcceptIn, db: Database, conn: Conn, actor: Actor = "local") -> list[dict]:
    accepted = conn.execute(
        "UPDATE relationships SET status = 'accepted'"
        " WHERE database_id = ? AND status = 'proposed' AND confidence >= ? RETURNING *",
        (db["id"], body.min_confidence),
    )
    rows = sorted(accepted, key=lambda r: (-r["confidence"], r["id"]))  # RETURNING has no ORDER BY
    for row in rows:
        _log(conn, row, "status", f"proposed -> accepted (bulk >= {body.min_confidence})", actor)
    return [dict(row) for row in rows]


@router.get("/databases/{database_id}/relationships/log", response_model=list[LogEntry])
def read_log(db: Database, conn: Conn) -> list[dict]:
    # Newest first by id, because two entries can share the same millisecond in `at`
    rows = conn.execute("SELECT * FROM relationship_log WHERE database_id = ? ORDER BY id DESC", (db["id"],))
    return [dict(row) for row in rows]


@router.get("/databases/{database_id}/path", response_model=list[PathStep] | None)
def join_path(source: str, target: str, db: Database, conn: Conn) -> list[dict] | None:
    """JOIN steps between two tables over the reviewed graph; null when they are not connected."""
    graph = load_graph(conn, db["id"])
    try:
        return find_path(graph, source, target)
    except nx.NodeNotFound:
        raise HTTPException(404, "table not found") from None
