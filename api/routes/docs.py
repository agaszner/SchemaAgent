"""Table documentation: read, edit or approve, regenerate, and export as Markdown."""

import sqlite3

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.appdb import latest_result, load_relationships
from api.config import settings
from api.deps import Conn, Database
from api.jobs import create_runs, execute
from api.payloads import DocIn, DocOut, RegenerateIn, RunOut
from api.routes.pipeline import SELECT_RUNS
from core.documentation import doc_basis, markdown_filename, overview_markdown, table_markdown

router = APIRouter(prefix="/databases/{database_id}", tags=["docs"])


def _docs(conn: sqlite3.Connection, database_id: int) -> dict[str, sqlite3.Row]:
    return {r["table_name"]: r for r in conn.execute(
        "SELECT * FROM table_docs WHERE database_id = ?", (database_id,))}


def _out(row: sqlite3.Row, tables: dict[str, dict], rels: list[dict]) -> dict:
    table = tables.get(row["table_name"])  # a table gone from the model is stale
    stale = table is None or row["basis"] != doc_basis(table, rels)
    return {**dict(row), "table": row["table_name"], "stale": stale}


def _model(conn: sqlite3.Connection, database_id: int) -> dict[str, dict]:
    return {t["name"]: t for t in latest_result(conn, database_id, "parse")["tables"]}


@router.get("/docs", response_model=list[DocOut])
def list_docs(db: Database, conn: Conn) -> list[dict]:
    docs = _docs(conn, db["id"])
    if not docs:
        return []
    tables, rels = _model(conn, db["id"]), load_relationships(conn, db["id"])
    order = list(tables) + [n for n in docs if n not in tables]  # orphans last
    return [_out(docs[n], tables, rels) for n in order if n in docs]


# POST routes first: "/docs/{table}" must not swallow "regenerate" or "export"
@router.post("/docs/regenerate", status_code=202, response_model=list[RunOut])
def regenerate(body: RegenerateIn, db: Database, conn: Conn, background_tasks: BackgroundTasks) -> list[dict]:
    known = _model(conn, db["id"]).keys() | _docs(conn, db["id"]).keys()  # a stored doc of a gone table is just deleted
    for name in body.tables:
        if name not in known:
            raise HTTPException(422, f"unknown table {name}")
    try:
        ids = create_runs(conn, db["id"], "docs")  # before the delete: a 409 leaves the docs intact
    except sqlite3.IntegrityError:
        raise HTTPException(409, "a pipeline run is already in progress") from None
    conn.executemany("DELETE FROM table_docs WHERE database_id = ? AND table_name = ?",
                     [(db["id"], name) for name in body.tables])
    background_tasks.add_task(execute, ids)
    return [dict(conn.execute(f"{SELECT_RUNS} WHERE id = ?", (run_id,)).fetchone()) for run_id in ids]


@router.post("/docs/export")
def export(db: Database, conn: Conn) -> dict:
    docs = _docs(conn, db["id"])
    if not docs:
        raise HTTPException(409, "run docs first")
    model, rels = latest_result(conn, db["id"], "parse"), load_relationships(conn, db["id"])
    tables_dir = (settings.export_dir / "tables").resolve()
    tables_dir.mkdir(parents=True, exist_ok=True)
    for old in tables_dir.glob("*.md"):  # renamed tables leave no leftovers; other files stay
        old.unlink()
    written = []
    for table in model["tables"]:
        if table["name"] not in docs:
            continue
        path = (tables_dir / markdown_filename(table["name"])).resolve()
        if path.parent != tables_dir:
            raise HTTPException(500, f"unsafe file name for table {table['name']}")
        path.write_text(table_markdown(table, dict(docs[table["name"]]), rels), encoding="utf-8")
        written.append(str(path))
    overview = tables_dir.parent / "schema_overview.md"
    overview.write_text(overview_markdown(db["name"], model, {n: dict(d) for n, d in docs.items()}, rels),
                        encoding="utf-8")
    return {"written": [*written, str(overview)]}


@router.get("/docs/{table}", response_model=DocOut)
def read_doc(table: str, db: Database, conn: Conn) -> dict:
    row = _docs(conn, db["id"]).get(table)
    if row is None:
        raise HTTPException(404, "doc not found")
    return _out(row, _model(conn, db["id"]), load_relationships(conn, db["id"]))


@router.put("/docs/{table}", response_model=DocOut)
def write_doc(table: str, body: DocIn, db: Database, conn: Conn) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    if table not in _docs(conn, db["id"]):
        raise HTTPException(404, "doc not found")
    model = _model(conn, db["id"])
    if table not in model:
        raise HTTPException(404, "table not in the model")
    names = [c["name"] for c in model[table]["columns"]]
    given = {c.name: c.model_dump() for c in body.columns}
    if len(body.columns) != len(given) or set(given) != set(names):
        raise HTTPException(422, "columns must be exactly the table's columns")
    rels = load_relationships(conn, db["id"])
    conn.execute(
        "UPDATE table_docs SET summary = ?, columns = ?, status = ?, basis = ?,"
        " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE database_id = ? AND table_name = ?",
        (body.summary, [given[n] for n in names], body.status, doc_basis(model[table], rels), db["id"], table))
    return _out(_docs(conn, db["id"])[table], model, rels)
