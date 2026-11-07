import sqlite3
from contextlib import closing

from api import llm
from api.appdb import connect
from api.config import settings

REAL_CLIENT = llm.client  # conftest patches it only inside fixtures


def docs(client, db):
    with closing(connect()) as conn:
        return {r["table_name"]: dict(r) for r in conn.execute("SELECT * FROM table_docs WHERE database_id = ?", (db,))}


def test_docs_step_documents_every_table(client, chinook, fake_llm):
    run = client.get(f"/api/databases/{chinook}/runs").json()[0]
    assert (run["step"], run["status"], run["done"], run["total"]) == ("docs", "done", 11, 11)
    rows = docs(client, chinook)
    assert len(rows) == 11 and {r["status"] for r in rows.values()} == {"generated"}
    assert len(fake_llm.calls) == 11 and fake_llm.calls[0]["model"] == "gpt-6-luna"
    assert round(sum(r["cost_usd"] for r in rows.values()), 8) == 0.00022  # 11 x (100 x 0.10 + 20 x 0.50) / 1e6


def test_rerun_skips_fresh_and_human_docs(client, chinook, fake_llm):
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE table_docs SET status = 'edited' WHERE database_id = ? AND table_name = 'Album'",
                     (chinook,))
        conn.execute("UPDATE table_docs SET status = 'approved' WHERE database_id = ? AND table_name = 'Artist'",
                     (chinook,))
    assert client.post(f"/api/databases/{chinook}/runs", json={"step": "docs"}).status_code == 202
    assert len(fake_llm.calls) == 11  # nothing regenerated, no LLM call
    rows = docs(client, chinook)
    assert (rows["Album"]["status"], rows["Artist"]["status"]) == ("edited", "approved")
    run = client.get(f"/api/databases/{chinook}/runs").json()[0]
    assert (run["step"], run["status"], run["done"], run["total"]) == ("docs", "done", 0, 0)


def test_relationship_change_regenerates_only_stale_generated_docs(client, shop, fake_llm):
    assert len(fake_llm.calls) == 2
    rel = next(r for r in client.get(f"/api/databases/{shop}/relationships").json() if r["to_table"] == "Customer")
    client.patch(f"/api/relationships/{rel['id']}", json={"status": "accepted"})
    client.post(f"/api/databases/{shop}/runs", json={"step": "docs"})
    assert len(fake_llm.calls) == 4
    assert {c["input"].count('"accepted"') for c in fake_llm.calls[2:]} == {1}
    rows = docs(client, shop)
    for table in ("Customer", "Orders"):
        assert ["Orders", ["CustomerId"], "Customer", ["CustomerId"], "accepted"] in rows[table]["basis"]["relationships"]


def test_docs_step_without_key_fails_clearly(client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "client", REAL_CLIENT)
    monkeypatch.setattr(settings, "openai_api_key", None)
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    docs_run, *rest = client.get(f"/api/databases/{db}/runs").json()
    assert docs_run["status"] == "error" and "OPENAI_API_KEY" in docs_run["error"]
    assert [(r["step"], r["status"]) for r in rest] == [("graph", "done"), ("profile", "done"), ("parse", "done")]


def test_docs_needs_graph_first(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    assert client.post(f"/api/databases/{db}/runs", json={"step": "docs"}).status_code == 409


def test_marking_a_column_deletes_that_tables_doc(client, chinook):
    url = f"/api/databases/{chinook}/masking"
    client.put(url, json={"columns": [{"table": "Customer", "column": "City"}]})
    rows = docs(client, chinook)
    assert "Customer" not in rows and "Album" in rows
    client.put(url, json={"columns": []})
    assert docs(client, chinook) == rows  # removing the mark deletes nothing more


def test_docs_step_holds_no_lock_during_the_llm_call(client, shop, fake_llm, monkeypatch):
    class EditingFake(type(fake_llm)):
        def parse(self, **kwargs):  # a relationship edit while the model answers must not hit "database is locked"
            with closing(sqlite3.connect(settings.app_db, timeout=0)) as conn, conn:
                conn.execute("UPDATE relationships SET note = 'edited' WHERE database_id = ?", (shop,))
            return super().parse(**kwargs)

    editing = EditingFake()
    monkeypatch.setattr(llm, "client", lambda: editing)
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM table_docs WHERE database_id = ?", (shop,))
    client.post(f"/api/databases/{shop}/runs", json={"step": "docs"})
    run = client.get(f"/api/databases/{shop}/runs").json()[0]
    assert (run["status"], run["error"], run["done"]) == ("done", None, 2)


def test_docs_after_parse_alone_asks_for_profile(client, parse_only_change, fake_llm):
    calls = len(fake_llm.calls)
    client.post(f"/api/databases/{parse_only_change}/runs", json={"step": "docs"})
    run = client.get(f"/api/databases/{parse_only_change}/runs").json()[0]
    assert (run["step"], run["status"]) == ("docs", "error") and "run profile first" in run["error"]
    assert len(fake_llm.calls) == calls
