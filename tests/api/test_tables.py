import sqlite3
from contextlib import closing

from ui import api_client


def test_rows_pages_track(client, chinook):
    body = client.get(f"/api/databases/{chinook}/tables/Track/rows", params={"limit": 50, "offset": 50}).json()
    assert body["total"] == 3503 and len(body["rows"]) == 50 and body["rows"][0]["TrackId"] == 51


def test_rows_errors(client, chinook, shop_db):
    assert client.get(f"/api/databases/{chinook}/tables/Nope/rows").status_code == 404
    r = client.get(f"/api/databases/{chinook}/tables/Customer/rows", params={"column": "Email", "value": "a"})
    assert r.status_code == 400
    assert client.get(f"/api/databases/{chinook}/tables/Track/rows", params={"limit": 101}).status_code == 400
    fresh = client.post("/api/databases", json={"path": str(shop_db)}).json()["id"]  # registered, never parsed
    assert client.get(f"/api/databases/{fresh}/tables/Orders/rows").status_code == 409


def test_rows_mask_user_marked_columns(client, chinook):
    client.put(f"/api/databases/{chinook}/masking", json={"columns": [{"table": "Customer", "column": "City"}]})
    rows = client.get(f"/api/databases/{chinook}/tables/Customer/rows").json()["rows"]
    assert {r["City"] for r in rows} == {"***"}


def _spaced(client, tmp_path) -> int:
    path = tmp_path / "spaced.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('CREATE TABLE "Order Details" (Id INTEGER PRIMARY KEY, Qty INTEGER);'
                           'INSERT INTO "Order Details" VALUES (1, 5), (2, 6), (3, 7);')
    db = client.post("/api/databases", json={"path": str(path)}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "parse"})
    return db


def test_rows_of_table_with_space(client, tmp_path):
    db = _spaced(client, tmp_path)
    assert client.get(f"/api/databases/{db}/tables/Order%20Details/rows").json()["total"] == 3


def test_api_client_rows(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "http", client)
    db = _spaced(client, tmp_path)
    assert api_client.rows(db, "Order Details")["total"] == 3


def test_rows_user_marked_column_refused_for_sort_and_filter(client, chinook):
    client.put(f"/api/databases/{chinook}/masking", json={"columns": [{"table": "Customer", "column": "City"}]})
    url = f"/api/databases/{chinook}/tables/Customer/rows"
    assert client.get(url, params={"sort": "City"}).status_code == 400
    assert client.get(url, params={"column": "City", "value": "x"}).status_code == 400
    assert client.get(url, params={"sort": "Country"}).status_code == 200
    assert "Country" not in client.get(url).json()["masked"]


def test_rows_409_when_table_dropped_since_parse(client, tmp_path):
    db = _spaced(client, tmp_path)
    with closing(sqlite3.connect(tmp_path / "spaced.db")) as conn, conn:
        conn.execute('DROP TABLE "Order Details"')
    assert client.get(f"/api/databases/{db}/tables/Order%20Details/rows").status_code == 409
