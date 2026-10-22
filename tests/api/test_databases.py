import json
import shutil
import sqlite3
from contextlib import closing

from api.config import settings


def test_register_list_and_get(client, tmp_path):
    r = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")})
    assert r.status_code == 201
    db = r.json()
    assert db["name"] == "chinook" and db["masked_columns"] == []
    assert client.get("/api/databases").json() == [db]
    assert client.get(f"/api/databases/{db['id']}").json() == db
    assert client.get("/api/databases/999").status_code == 404


def test_register_rejects_duplicates(client, tmp_path):
    body = {"path": str(tmp_path / "chinook.db")}
    assert client.post("/api/databases", json=body).status_code == 201
    assert client.post("/api/databases", json=body).status_code == 409
    assert client.post("/api/databases", json={**body, "name": "other"}).status_code == 409


def test_register_rejects_invalid_paths(client, tmp_path, tmp_path_factory):
    (tmp_path / "notes.db").write_text("hello")
    (tmp_path / "folder.db").mkdir()
    outside = tmp_path_factory.mktemp("outside") / "chinook.db"
    shutil.copy(tmp_path / "chinook.db", outside)
    for path in [tmp_path / "missing.db", tmp_path / "folder.db", tmp_path / "notes.db", outside]:
        assert client.post("/api/databases", json={"path": str(path)}).status_code == 422, path
    assert client.post("/api/databases", json={"path": str(tmp_path / "bad\0.db")}).status_code == 422


def test_upload_stores_and_registers(client, tmp_path):
    data = (tmp_path / "chinook.db").read_bytes()
    r = client.post("/api/databases/upload", files={"file": ("music.db", data)})
    assert r.status_code == 201 and r.json()["name"] == "music"
    assert (tmp_path / "uploads" / "music.db").read_bytes() == data
    assert client.post("/api/databases/upload", files={"file": ("music.db", data)}).status_code == 409

    client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")})
    assert client.post("/api/databases/upload", files={"file": ("chinook.db", data)}).status_code == 409
    assert not (tmp_path / "uploads" / "chinook.db").exists()


def test_upload_rejects_bad_names_and_non_sqlite(client, tmp_path):
    data = (tmp_path / "chinook.db").read_bytes()
    for name in ["../evil.db", ".hidden.db", "notes.txt", "a" * 300 + ".db"]:
        assert client.post("/api/databases/upload", files={"file": (name, data)}).status_code == 422, name
    assert client.post("/api/databases/upload", files={"file": ("fake.db", b"hello")}).status_code == 422
    assert not (tmp_path / "evil.db").exists()
    assert list((tmp_path / "uploads").glob("*")) == []


def test_masking_a_column_hides_its_values_after_reprofiling(client, chinook):
    mark = [{"table": "Genre", "column": "Name"}]
    r = client.put(f"/api/databases/{chinook}/masking", json={"columns": mark})
    assert r.status_code == 200 and r.json()["masked_columns"] == mark
    assert next(r for r in client.get(f"/api/databases/{chinook}/runs").json() if r["step"] == "profile")["has_result"] is False
    assert client.get(f"/api/databases/{chinook}/profile").status_code == 409  # stale values dropped
    client.post(f"/api/databases/{chinook}/runs", json={"step": "profile"})
    assert next(r for r in client.get(f"/api/databases/{chinook}/runs").json() if r["step"] == "profile")["has_result"] is True
    tables = {t["table"]: t for t in client.get(f"/api/databases/{chinook}/profile").json()["tables"]}
    assert "Rock" not in json.dumps(tables["Genre"])
    assert not next(c for c in tables["Track"]["columns"] if c["name"] == "Name")["masked"]


def test_masking_rejects_unknown_columns_and_running_pipelines(client, chinook):
    url = f"/api/databases/{chinook}/masking"
    assert client.put(url, json={"columns": [{"table": "Genre", "column": "Nope"}]}).status_code == 422
    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (chinook,))
    assert client.put(url, json={"columns": []}).status_code == 409
