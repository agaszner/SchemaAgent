import sqlite3
from contextlib import closing

URL = "/api/databases/{}/docs"


def docs(client, db):
    return {d["table"]: d for d in client.get(URL.format(db)).json()}


def body(client, db, table="Album", status="approved"):
    d = client.get(f"{URL.format(db)}/{table}").json()
    return {"summary": d["summary"], "columns": d["columns"], "status": status}


def test_docs_round_trip_is_not_stale(client, chinook):
    got = client.get(URL.format(chinook)).json()
    assert len(got) == 11 and not any(d["stale"] for d in got)


def test_no_docs_without_parse(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    assert client.get(URL.format(db)).json() == []


def test_put_validates_and_approves(client, chinook):
    url = f"{URL.format(chinook)}/Album"
    r = client.put(url, json=body(client, chinook))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    b = body(client, chinook)
    b["columns"] = [c for c in b["columns"] if c["name"] != "Title"]
    assert client.put(url, json=b).status_code == 422
    assert client.put(f"{URL.format(chinook)}/Nope", json=body(client, chinook)).status_code == 404


def test_put_stores_model_order(client, chinook):
    b = body(client, chinook)
    b["columns"] = [{**c, "description": "t" if c["name"] == "Title" else c["description"]}
                    for c in reversed(b["columns"])]
    assert client.put(f"{URL.format(chinook)}/Album", json=b).status_code == 200
    cols = client.get(f"{URL.format(chinook)}/Album").json()["columns"]
    assert [c["name"] for c in cols] == ["AlbumId", "Title", "ArtistId"]
    assert cols[1]["description"] == "t"


def test_rejecting_a_relationship_makes_both_tables_stale_until_regenerated(client, chinook):
    rels = client.get(f"/api/databases/{chinook}/relationships").json()
    rel = next(r for r in rels if r["from_table"] == "Album")
    client.patch(f"/api/relationships/{rel['id']}", json={"status": "rejected"})
    stale = {n for n, d in docs(client, chinook).items() if d["stale"]}
    assert stale == {"Album", rel["to_table"]}
    r = client.post(f"{URL.format(chinook)}/regenerate", json={"tables": ["Album"]})
    assert r.status_code == 202
    album = docs(client, chinook)["Album"]
    assert album["status"] == "generated" and not album["stale"]


def test_regenerate_validates(client, chinook):
    assert client.post(f"{URL.format(chinook)}/regenerate", json={"tables": ["Nope"]}).status_code == 422
    assert client.post(f"{URL.format(chinook)}/regenerate", json={"tables": []}).status_code == 422


def test_regenerate_drops_a_doc_whose_table_is_gone(client, parse_only_change):
    assert docs(client, parse_only_change)["B"]["stale"]
    r = client.post(f"{URL.format(parse_only_change)}/regenerate", json={"tables": ["B"]})
    assert r.status_code == 202 and "B" not in docs(client, parse_only_change)


def test_export_writes_markdown(client, chinook, tmp_path):
    r = client.post(f"{URL.format(chinook)}/export")
    assert r.status_code == 200
    album = (tmp_path / "export/tables/Album.md").read_text()
    assert "# Album" in album and "Status: generated" in album
    assert "tables/Album.md" in (tmp_path / "export/schema_overview.md").read_text()
    assert len(r.json()["written"]) == 12


def test_export_without_docs_is_409(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    assert client.post(f"{URL.format(db)}/export").status_code == 409


def test_export_stays_inside_tables_dir(client, tmp_path):
    path = tmp_path / "evil.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE "../evil" (id INTEGER PRIMARY KEY)')
    db = client.post("/api/databases", json={"path": str(path)}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    assert client.post(f"{URL.format(db)}/export").status_code == 200
    export = tmp_path / "export"
    assert (export / "tables" / "___evil.md").exists()
    assert {p for p in export.rglob("*") if p.is_file()} == {
        export / "tables" / "___evil.md", export / "schema_overview.md"}
    assert not (tmp_path / "evil.md").exists()


def test_export_keeps_other_files(client, chinook, tmp_path):
    keep = tmp_path / "export/tables/keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("x")
    assert client.post(f"{URL.format(chinook)}/export").status_code == 200
    assert keep.exists()


def test_put_during_a_docs_run_survives(client, chinook, fake_llm):
    # two pending tables; while the first is generated, a person approves the second
    from api.config import settings
    with closing(sqlite3.connect(settings.app_db)) as c, c:
        c.execute("DELETE FROM table_docs WHERE table_name IN ('Album', 'Artist')")
    original = fake_llm.parse
    state = {"done": False}

    def parse(**kwargs):
        if not state["done"]:
            state["done"] = True
            with closing(sqlite3.connect(settings.app_db)) as c, c:
                c.execute("INSERT INTO table_docs (database_id, table_name, summary, columns, status, basis,"
                          " llm_model, input_tokens, output_tokens, cost_usd) SELECT ?, 'Artist', 'mine', '[]',"
                          " 'approved', '{}', 'm', 0, 0, 0", (chinook,))
        return original(**kwargs)

    fake_llm.parse = parse
    client.post(f"/api/databases/{chinook}/runs", json={"step": "docs"})
    artist = client.get(f"{URL.format(chinook)}/Artist").json()
    assert artist["status"] == "approved" and artist["summary"] == "mine"
