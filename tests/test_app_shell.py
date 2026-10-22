import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app import api, theme


def test_css_defines_tokens_for_both_themes():
    css = theme.css()
    assert css.startswith('@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono')
    assert ':root, [data-theme="light"] {' in css and '[data-theme="dark"] {' in css
    assert "--paper: #f7f6f2;" in css and "--paper: #161614;" in css
    assert "--focus-ring: var(--accent);" in css
    assert "--space-4: 16px;" in css and "--radius-round: 50%;" in css
    assert '--font-mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;' in css
    assert theme.color("focus-ring", "dark") == "#8fb0ec" and theme.color("edge-accepted") == "#3f8a52"


def test_request_returns_json_and_raises_backend_detail(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "http", client)
    db = api.request("POST", "/databases", json={"path": str(tmp_path / "chinook.db")})
    assert api.request("GET", f"/databases/{db['id']}") == db
    assert api.model(db["id"]) is None  # 409 until the parse step has run
    with pytest.raises(api.ApiError) as err:
        api.request("GET", "/databases/999")
    assert (err.value.status, err.value.detail) == (404, "database not found")
    with pytest.raises(api.ApiError) as err:
        api.request("POST", "/databases", json={})
    assert (err.value.status, err.value.detail) == (422, "Field required")


def test_home_lists_databases_with_counts(ui, client, chinook, shop_db):
    client.post("/api/databases", json={"path": str(shop_db)})  # registered, never run
    at = ui()
    picker = at.sidebar.selectbox(key="database_id")
    assert (picker.options, picker.value) == (["chinook", "shop"], chinook)
    table = at.dataframe[0].value
    assert table.iloc[0].to_dict() == {
        "Database": "chinook", "Tables": 11, "Columns": 64, "Declared FKs": 11, "Pipeline": "done"}
    assert table["Pipeline"].tolist() == ["done", "not run"]


def test_home_without_databases_shows_empty_state(ui):
    at = ui()
    assert not at.exception
    assert at.info[0].value.startswith("No database registered yet")
    assert at.button(key="manage_databases").label == "Manage databases"


def test_shell_reports_unreachable_api(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(api, "http", httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(refuse)))
    at = AppTest.from_file("../app/main.py", default_timeout=30).run()
    assert not at.exception
    assert at.error[0].value.startswith("Cannot reach the SchemaSage API at http://127.0.0.1:9")


def test_run_label_flags_cleared_output():
    from app import ui

    assert ui.run_label(None) == ("not run", "idle")
    assert ui.run_label({"status": "done", "has_result": True}) == ("done", "ok")
    assert ui.run_label({"status": "done", "has_result": False}) == ("cleared", "warn")
    assert ui.run_label({"status": "error", "has_result": False}) == ("failed", "error")


def test_home_summarises_and_sends_suggestions_to_review(ui, shop):
    at = ui(database_id=shop)
    html = " ".join(h.value for h in at.html)
    assert "2 suggested relationships" in html and "/ 2 to review" in html
    at.button(key="manage_databases").click().run()
    assert at.title[0].value == "Management" and at.subheader[0].value == "Registered databases"
    at.query_params.clear()
    at.switch_page("views/home.py").run()
    at.button(key="review_relationships").click().run()
    assert at.title[0].value == "Management" and at.subheader[0].value == "Relationships"


def test_home_points_to_the_failed_step(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    (tmp_path / "chinook.db").write_bytes(b"no longer a database")
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    at = ui(database_id=db)
    html = " ".join(h.value for h in at.html)
    assert "Parse step failed" in html and "DatabaseError" in html
    at.button(key="open_pipeline").click().run()
    assert at.title[0].value == "Pipeline"
