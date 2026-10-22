import sqlite3
from contextlib import closing

import httpx

from app import api
from app.graph import build_network
from backend.config import settings


def _steps(at):
    return next(h.value for h in at.html if "ss-steps" in h.value)


def test_pipeline_runs_every_step_and_logs_runs(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    at = ui("views/pipeline.py", database_id=db)
    assert _steps(at).count(">not run<") == 3
    at.button(key="run:all").click().run()
    assert _steps(at).count(">done<") == 3
    assert [line.split()[1] for line in at.code[0].value.splitlines()] == ["graph", "profile", "parse"]


def test_pipeline_shows_refusals_and_locks_while_running(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    at = ui("views/pipeline.py", database_id=db)
    at.button(key="run:graph").click().run()
    assert at.error[0].value == "run parse first"
    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (db,))
    at.run()
    assert ">running<" in _steps(at)
    assert all(at.button(key=k).disabled for k in ["run:all", "run:parse", "run:profile", "run:graph"])


def test_pipeline_without_databases_shows_empty_state(ui):
    at = ui("views/pipeline.py", database_id=999)
    assert not at.exception
    assert at.info[0].value.startswith("No database registered yet")


def test_fragment_reruns_when_active_run_finishes(ui, client, tmp_path, monkeypatch):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status, started_at) "
                     "VALUES (?, 'parse', 'running', '2026-10-21T10:00:00.000Z')", (db,))
    real, calls = api.request, []

    def request(method, path, **kw):
        out = real(method, path, **kw)
        if method == "GET" and path.endswith("/runs"):
            calls.append(path)
            if len(calls) == 1:  # the page header saw "running"; finish the run before the fragment polls
                with closing(sqlite3.connect(settings.app_db)) as conn, conn:
                    conn.execute("UPDATE runs SET status = 'done' WHERE database_id = ?", (db,))
        return out

    monkeypatch.setattr(api, "request", request)
    at = ui("views/pipeline.py", database_id=db)
    assert len(calls) >= 3, calls  # the fragment's st.rerun() ran the page again
    assert "running" not in at.code[0].value, at.code[0].value


def test_fragment_reports_unreachable_api(ui, client, tmp_path, monkeypatch):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (db,))
    real, calls = api.request, []

    def request(method, path, **kw):
        if method == "GET" and path.endswith("/runs"):
            calls.append(path)
            if len(calls) == 2:  # the page header got its runs; the backend goes away before the fragment polls
                raise httpx.ConnectError("connection refused")
        return real(method, path, **kw)

    monkeypatch.setattr(api, "request", request)
    at = ui("views/pipeline.py", database_id=db)
    assert len(calls) == 2, calls
    assert not at.exception
    assert at.error[0].value == "Cannot reach the SchemaSage API at http://testserver. Start it with `uv run fastapi dev`."


def _rel(from_table, to_table, source, status):
    return {"from_table": from_table, "from_columns": ["a"], "to_table": to_table, "to_columns": ["b"],
            "source": source, "status": status, "confidence": 0.8}


def test_network_styles_edges_by_source_and_status():
    model = {"tables": [{"name": "Invoice"}, {"name": "Customer"}, {"name": "Orders"}]}
    rels = [_rel("Invoice", "Customer", "declared", "accepted"), _rel("Orders", "Customer", "inferred", "accepted"),
            _rel("Orders", "Invoice", "inferred", "proposed"), _rel("Invoice", "Orders", "inferred", "rejected"),
            _rel("Customer", "Orders", "manual", "accepted"), _rel("Orders", "Ghost", "declared", "accepted")]
    net = build_network(model, rels, "light")
    assert [n["id"] for n in net.nodes] == ["Invoice", "Customer", "Orders", "Ghost"]
    assert [(e["from"], e["to"], e["color"], e["width"], e["dashes"]) for e in net.edges] == [
        ("Invoice", "Customer", "#24479a", 2, False),
        ("Orders", "Customer", "#3f8a52", 3, False),
        ("Orders", "Invoice", "#8a867b", 2, [6, 4]),
        ("Customer", "Orders", "#3f8a52", 3, False),
        ("Orders", "Ghost", "#24479a", 2, False)]
    assert build_network(model, rels[:1], "dark").edges[0]["color"] == "#8fb0ec"
    hostile = build_network(model, [_rel("<img src=x>", "Customer", "declared", "accepted")], "light")
    assert hostile.edges[0]["title"].startswith("&lt;img src=x&gt;.a → Customer.b")


def test_pipeline_draws_graph_with_legend(ui, chinook):
    at = ui("views/pipeline.py", database_id=chinook)
    assert len(at.get("iframe")) == 1
    assert "declared FK" in next(h.value for h in at.html if "ss-legend" in h.value)
