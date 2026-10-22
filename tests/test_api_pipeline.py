import json
import sqlite3
from contextlib import closing

from backend.config import settings
from backend.storage import init_db


def _insert_running_run(database_id):
    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (database_id,))


def _relationships(database_id):
    with closing(sqlite3.connect(settings.app_db)) as conn:
        return conn.execute("SELECT id, source, status, confidence FROM relationships WHERE database_id = ?"
                            " ORDER BY confidence DESC", (database_id,)).fetchall()


def test_full_run_builds_model_profile_and_relationships(client, chinook):
    runs = client.get(f"/api/databases/{chinook}/runs").json()
    assert [(r["step"], r["status"], r["error"]) for r in runs] == [
        ("graph", "done", None), ("profile", "done", None), ("parse", "done", None)]
    assert all(r["duration_s"] >= 0 for r in runs)
    assert len(client.get(f"/api/databases/{chinook}/model").json()["tables"]) == 11
    assert "@" not in json.dumps(client.get(f"/api/databases/{chinook}/profile").json())
    with closing(sqlite3.connect(settings.app_db)) as conn:
        assert conn.execute("SELECT source, status, COUNT(*) FROM relationships GROUP BY 1, 2").fetchall() == [
            ("declared", "accepted", 11)]


def test_steps_need_their_prerequisites(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    r = client.post(f"/api/databases/{db}/runs", json={"step": "graph"})
    assert r.status_code == 409 and r.json()["detail"] == "run parse first"
    assert client.get(f"/api/databases/{db}/model").status_code == 409
    assert client.post(f"/api/databases/{db}/runs", json={"step": "docs"}).status_code == 422
    r = client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    assert r.status_code == 202
    assert [(x["step"], x["status"]) for x in r.json()] == [
        ("parse", "running"), ("profile", "queued"), ("graph", "queued")]


def test_only_one_pipeline_runs_per_database(client, chinook):
    _insert_running_run(chinook)
    assert client.post(f"/api/databases/{chinook}/runs", json={"step": "all"}).status_code == 409


def test_failed_step_records_error_and_skips_the_rest(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    (tmp_path / "chinook.db").write_bytes(b"no longer a database")
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    parse, profile, graph = reversed(client.get(f"/api/databases/{db}/runs").json())
    assert parse["status"] == "error" and parse["error"].startswith("DatabaseError")
    assert (profile["status"], profile["error"]) == ("error", "skipped: parse failed")
    assert (graph["status"], graph["error"]) == ("error", "skipped: parse failed")


def test_restart_marks_unfinished_runs_interrupted(client, chinook):
    _insert_running_run(chinook)
    init_db()
    latest = client.get(f"/api/databases/{chinook}/runs").json()[0]
    assert (latest["status"], latest["error"]) == ("error", "interrupted by server restart")


def test_rerun_seeds_each_candidate_once(client, shop):
    seeded = _relationships(shop)
    assert [r[1:] for r in seeded] == [("inferred", "proposed", 1.0), ("inferred", "proposed", 0.8)]
    assert client.post(f"/api/databases/{shop}/runs", json={"step": "all"}).status_code == 202
    graph = client.get(f"/api/databases/{shop}/runs").json()[0]
    assert (graph["step"], graph["status"]) == ("graph", "done")  # the stored pairs did not fail the step
    assert _relationships(shop) == seeded


def test_rerun_drops_stale_suggestions_but_keeps_reviewed_ones(client, shop):
    def pairs():
        with closing(sqlite3.connect(settings.app_db)) as conn:
            return set(conn.execute("SELECT from_columns, to_table, status FROM relationships WHERE database_id = ?",
                                    (shop,)))

    with closing(sqlite3.connect(settings.app_db)) as conn, conn:  # pairs an older finder proposed
        conn.executemany(
            "INSERT INTO relationships (database_id, from_table, from_columns, to_table, to_columns, source, status)"
            " VALUES (?, 'Orders', '[\"Qty\"]', ?, '[\"OrderId\"]', 'inferred', ?)",
            [(shop, "Customer", "proposed"), (shop, "Orders", "rejected")])
    before = pairs()
    client.post(f"/api/databases/{shop}/runs", json={"step": "graph"})
    assert before - pairs() == {('["Qty"]', "Customer", "proposed")}
