from ui.views.management_tabs.relationships import actions, bulk_count, visible


def test_actions_follow_source_and_status():
    assert actions("declared", "accepted") == ["disable"]
    assert actions("declared", "rejected") == ["accept"]
    assert actions("inferred", "proposed") == ["accept", "reject"]
    assert actions("inferred", "accepted") == ["delete"]
    assert actions("inferred", "rejected") == ["accept"]
    assert actions("manual", "accepted") == ["delete"]


def test_filters_and_bulk_count():
    rels = [{"source": "declared", "status": "accepted", "confidence": 1.0},
            {"source": "inferred", "status": "proposed", "confidence": 0.95},
            {"source": "inferred", "status": "proposed", "confidence": 0.6},
            {"source": "inferred", "status": "rejected", "confidence": 0.99}]
    assert visible(rels, "inferred", "all", 0.9) == [rels[1], rels[3]]
    assert visible(rels, "all", "proposed", 0.0) == rels[1:3]
    assert (bulk_count(rels, 0.9), bulk_count(rels, 0.0)) == (1, 2)


def test_review_rejects_bulk_accepts_and_logs(ui, client, shop):
    url = f"/api/databases/{shop}/relationships"
    strong, weak = client.get(url).json()  # confidence 1.0 and 0.8
    at = ui("views/management.py", database_id=shop, tab="Relationships")
    at.button(key=f"reject:{weak['id']}").click().run()
    assert f"accept:{weak['id']}" in [b.key for b in at.button]  # a rejected row can be accepted again
    assert "proposed -&gt; rejected · local · " in " ".join(h.value for h in at.html)
    at.slider(key="rel_confidence").set_value(0.9).run()
    assert at.button(key="bulk_accept").label == "Accept all ≥ 0.90 (1)"
    at.button(key="bulk_accept").click().run()
    assert {r["id"]: r["status"] for r in client.get(url).json()} == {strong["id"]: "accepted", weak["id"]: "rejected"}
    assert at.dataframe[0].value["Detail"].tolist() == ["proposed -> accepted (bulk >= 0.9)", "proposed -> rejected"]


def test_declared_fk_is_disabled_only_after_confirmation(ui, client, chinook):
    url = f"/api/databases/{chinook}/relationships"
    rel = client.get(url).json()[0]

    def status():
        return next(r["status"] for r in client.get(url).json() if r["id"] == rel["id"])

    at = ui("views/management.py", database_id=chinook, tab="Relationships")
    keys = [b.key for b in at.button]
    assert f"disable:{rel['id']}" in keys and f"delete:{rel['id']}" not in keys
    at.button(key=f"disable:{rel['id']}").click().run()
    assert status() == "accepted"
    assert "Declared foreign keys come from the schema" in " ".join(h.value for h in at.html)
    at.button(key=f"confirm_disable:{rel['id']}").click().run()
    assert status() == "rejected"
    at.button(key=f"accept:{rel['id']}").click().run()
    assert status() == "accepted"


def _fill(at, from_table, from_column, to_table, to_column):
    at.selectbox(key="nr_from_table").select(from_table).run()
    at.multiselect(key="nr_from_columns").select(from_column).run()
    at.selectbox(key="nr_to_table").select(to_table).run()
    return at.multiselect(key="nr_to_columns").select(to_column).run()


def _check(at):
    return next(h.value for h in at.html if "Value match" in h.value)


def test_check_passes_and_duplicates_are_refused(ui, chinook):
    at = _fill(ui("views/management.py", database_id=chinook, tab="Relationships"),
               "Invoice", "CustomerId", "Customer", "CustomerId")
    assert "100% of Invoice.CustomerId values exist in Customer.CustomerId." in _check(at)
    assert "INT → INT" in _check(at)
    at.button(key="nr_save").click().run()
    assert at.error[0].value.startswith("relationship already exists (id ")
    assert at.error[0].value.endswith(", accepted)")


def test_low_match_warns_but_saves_and_manual_rows_delete(ui, client, chinook):
    url = f"/api/databases/{chinook}/relationships"
    at = ui("views/management.py", database_id=chinook, tab="Relationships")
    at.selectbox(key="nr_from_table").select("Track").run()
    at.multiselect(key="nr_from_columns").select("GenreId").run()
    at.selectbox(key="nr_from_table").select("Invoice").run()
    assert at.multiselect(key="nr_from_columns").value == []  # a new table clears its columns
    _fill(at, "Track", "GenreId", "MediaType", "MediaTypeId")
    assert "only 20% of Track.GenreId values exist in MediaType.MediaTypeId" in _check(at)
    at.text_input(key="nr_note").input("demo")
    at.button(key="nr_save").click().run()
    manual = next(r for r in client.get(url).json() if r["source"] == "manual")
    assert (manual["from_table"], manual["to_table"], manual["note"], manual["status"]) == (
        "Track", "MediaType", "demo", "accepted")
    assert at.selectbox(key="nr_from_table").value is None  # the form resets after saving
    at.button(key=f"delete:{manual['id']}").click().run()
    assert all(r["source"] != "manual" for r in client.get(url).json())
