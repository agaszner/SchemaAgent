"""Registry of analysed SQLite databases; only files inside the data dir can be registered."""

import re
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from backend.config import settings
from backend.dependencies import Conn, Database
from backend.schemas import DatabaseIn, DatabaseOut, MaskingIn
from backend.storage import latest_result

router = APIRouter(prefix="/databases", tags=["databases"])


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(16) == b"SQLite format 3\x00"


def _register(conn: sqlite3.Connection, path: Path, name: str) -> dict:
    try:
        row = conn.execute(
            "INSERT INTO databases (name, path) VALUES (?, ?) RETURNING *", (name, str(path))
        ).fetchone()
    except sqlite3.IntegrityError:  # name or path already taken
        raise HTTPException(409, "already registered") from None
    return dict(row)


@router.get("", response_model=list[DatabaseOut])
def list_databases(conn: Conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM databases ORDER BY id")]


@router.post("", status_code=201, response_model=DatabaseOut)
def register_database(body: DatabaseIn, conn: Conn) -> dict:
    try:
        path = Path(body.path).resolve()  # relative paths resolve against the server's working directory
        # Checked first, so files outside the data dir are never opened
        if not path.is_relative_to(settings.data_dir.resolve()):
            raise HTTPException(422, f"path must be inside {settings.data_dir}")
        if not path.is_file():
            raise HTTPException(422, "no such file")
        if not _is_sqlite(path):
            raise HTTPException(422, "not a SQLite database")
    except (ValueError, OSError, RuntimeError):  # NUL byte, name too long, unreadable file, symlink loop on 3.12
        raise HTTPException(422, "invalid path") from None
    return _register(conn, path, body.name or path.stem)


@router.post("/upload", status_code=201, response_model=DatabaseOut)
def upload_database(file: UploadFile, conn: Conn) -> dict:
    name = file.filename or ""
    # Checked before the disk is touched; the name can hold no path separator and no leading dot
    if not re.fullmatch(r"[A-Za-z0-9][\w-]*\.(db|sqlite3?)", name):
        raise HTTPException(422, "invalid file name")
    path = settings.data_dir / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = path.open("xb")  # exclusive create: never overwrites or follows an existing entry
    except FileExistsError:
        raise HTTPException(409, "file exists") from None
    except OSError:  # e.g. a name longer than the file system allows
        raise HTTPException(422, "invalid file name") from None
    try:
        with out:
            shutil.copyfileobj(file.file, out)
        if not _is_sqlite(path):
            raise HTTPException(422, "not a SQLite database")
        return _register(conn, path.resolve(), path.stem)
    except BaseException:  # a rejected upload leaves nothing on disk
        path.unlink()
        raise


@router.get("/{database_id}", response_model=DatabaseOut)
def read_database(db: Database) -> dict:
    return dict(db)


@router.put("/{database_id}/masking", response_model=DatabaseOut)
def set_masking(body: MaskingIn, db: Database, conn: Conn) -> dict:
    # Write lock before the check: a concurrent POST /runs waits for this commit, so its profile reads the new marks
    conn.execute("BEGIN IMMEDIATE")
    # A profile in flight would cache values under the old marks
    in_progress = "SELECT 1 FROM runs WHERE database_id = ? AND status IN ('queued', 'running') LIMIT 1"
    if conn.execute(in_progress, (db["id"],)).fetchone():
        raise HTTPException(409, "a pipeline run is in progress")
    # Exact, case-sensitive model names: profile_database matches marks exactly, so every stored mark applies
    model = latest_result(conn, db["id"], "parse")
    known = {t["name"]: {c["name"] for c in t["columns"]} for t in model["tables"]}
    for mark in body.columns:
        if mark.column not in known.get(mark.table, ()):
            raise HTTPException(422, f"unknown column {mark.table}.{mark.column}")
    row = conn.execute(
        "UPDATE databases SET masked_columns = ? WHERE id = ? RETURNING *",
        ([mark.model_dump() for mark in body.columns], db["id"]),
    ).fetchone()
    # The cached profiles hold the newly marked columns' raw values; GET /profile answers 409 until a re-run
    conn.execute("UPDATE runs SET result = NULL WHERE database_id = ? AND step = 'profile'", (db["id"],))
    return dict(row)
