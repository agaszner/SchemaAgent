def _tab(ui, db):
    return ui("views/management.py", database_id=db, tab="Documentation")


def test_tab_without_docs_points_to_pipeline(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    at = _tab(ui, db)
    assert not at.exception and "Pipeline" in at.info[0].value


def test_save_then_approve(ui, client, chinook):
    at = _tab(ui, chinook)
    assert not at.exception and at.caption[1].value.startswith("11 tables · 0 approved · 0 stale")
    at.selectbox(key="docs:table").select("Album").run()
    at.text_area(key="docs:summary:Album").set_value("Records released by artists.")
    at.button(key="docs:save").click().run()
    doc = client.get(f"/api/databases/{chinook}/docs/Album").json()
    assert (doc["summary"], doc["status"]) == ("Records released by artists.", "edited")
    at.selectbox(key="docs:cert:Album:Title").select("certain")
    at.button(key="docs:approve").click().run()
    doc = client.get(f"/api/databases/{chinook}/docs/Album").json()
    assert doc["status"] == "approved" and doc["columns"][1]["certainty"] == "certain"


def test_regenerate_stale_tables(ui, client, chinook):
    at = _tab(ui, chinook)
    assert at.button(key="docs:regenerate").disabled
    rel = next(r for r in client.get(f"/api/databases/{chinook}/relationships").json()
               if "Album" in (r["from_table"], r["to_table"]))
    client.patch(f"/api/relationships/{rel['id']}", json={"status": "rejected"})
    at.run()
    assert not at.button(key="docs:regenerate").disabled
    before = len(client.get(f"/api/databases/{chinook}/runs").json())
    at.button(key="docs:regenerate").click().run()
    runs = client.get(f"/api/databases/{chinook}/runs").json()
    assert len(runs) > before and runs[0]["step"] == "docs"
    assert not any(d["stale"] for d in client.get(f"/api/databases/{chinook}/docs").json())


def test_export_writes_files(ui, client, chinook, tmp_path):
    at = _tab(ui, chinook)
    at.button(key="docs:export").click().run()
    assert at.success and (tmp_path / "export" / "schema_overview.md").exists()


def test_server_side_change_replaces_widget_text(ui, client, chinook):
    at = _tab(ui, chinook)
    at.selectbox(key="docs:table").select("Album").run()
    assert at.text_area(key="docs:summary:Album").value == "Fake summary."
    url = f"/api/databases/{chinook}/docs/Album"
    doc = client.get(url).json()
    client.put(url, json={**doc, "summary": "Changed elsewhere.", "status": "edited"})
    at.run()
    assert at.text_area(key="docs:summary:Album").value == "Changed elsewhere."


def test_regenerate_conflict_shows_error(ui, client, chinook, monkeypatch):
    from ui import api_client
    rel = next(r for r in client.get(f"/api/databases/{chinook}/relationships").json() if r["from_table"] == "Album")
    client.patch(f"/api/relationships/{rel['id']}", json={"status": "rejected"})
    real = api_client.request

    def request(method, path, **kw):
        if method == "POST" and path.endswith("/docs/regenerate"):
            raise api_client.ApiError(409, "a run is in progress")
        return real(method, path, **kw)

    monkeypatch.setattr(api_client, "request", request)
    at = _tab(ui, chinook)
    at.button(key="docs:regenerate").click().run()
    assert at.error[0].value == "a run is in progress"


def test_likely_certainty_renders_and_saves(ui, client, chinook, fake_llm):
    fake_llm.reply = {"summary": "S.", "columns": [{"name": "Title", "description": "d", "certainty": "likely"}]}
    client.post(f"/api/databases/{chinook}/docs/regenerate", json={"tables": ["Album"]})
    at = _tab(ui, chinook)
    at.selectbox(key="docs:table").select("Album").run()
    assert not at.exception and at.selectbox(key="docs:cert:Album:Title").value == "likely"
    at.button(key="docs:save").click().run()
    doc = client.get(f"/api/databases/{chinook}/docs/Album").json()
    assert (doc["status"], doc["columns"][1]["certainty"]) == ("edited", "likely")
