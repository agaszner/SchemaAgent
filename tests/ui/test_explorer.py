def test_explorer_lists_tables_and_profiles_customer(ui, chinook):
    at = ui("views/explorer.py", database_id=chinook)
    assert not at.exception and at.title[0].value == "Explorer"
    tables = at.radio(key="explorer_table")
    assert "Track · 3,503" in tables.options and tables.value == "Album"  # first table by default
    tables.set_value("Customer").run()
    profile = at.dataframe[0].value
    email = profile[profile["Column"] == "Email"].iloc[0]
    assert profile.iloc[0]["Top values"] == "every value distinct" and profile.iloc[0]["Column"] == "CustomerId"
    assert email["Range"] == "***" and email["Top values"] == "masked"
    assert "59 rows · 13 columns · primary key CustomerId" in [c.value for c in at.caption]


def test_explorer_relationship_chip_opens_table(ui, chinook):
    at = ui("views/explorer.py", database_id=chinook)
    at.radio(key="explorer_table").set_value("Customer").run()
    at.button(key="open:Employee").click().run()
    assert at.radio(key="explorer_table").value == "Employee"


def test_explorer_explains_missing_steps(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    at = ui("views/explorer.py", database_id=db)
    assert not at.exception and at.info[0].value.startswith("Run parse first")
    client.post(f"/api/databases/{db}/runs", json={"step": "parse"})
    at.run()
    assert any(i.value.startswith("Run profile") for i in at.info)


def test_explorer_survives_profile_older_than_model(ui, chinook):
    import json
    import sqlite3
    from contextlib import closing

    from api.config import settings

    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        raw, rid = conn.execute(
            "SELECT result, id FROM runs WHERE database_id = ? AND step = 'profile' ORDER BY id DESC", (chinook,)).fetchone()
        result = json.loads(raw)
        next(t for t in result["tables"] if t["table"] == "Album")["columns"].pop()
        conn.execute("UPDATE runs SET result = ? WHERE id = ?", (json.dumps(result), rid))
    at = ui("views/explorer.py", database_id=chinook)
    assert not at.exception
    assert any(i.value.startswith("Run profile") for i in at.info)


def _open(ui, chinook, table):
    at = ui("views/explorer.py", database_id=chinook)
    at.radio(key="explorer_table").set_value(table).run()
    return at


def test_data_tab_pages_through_track(ui, chinook):
    at = _open(ui, chinook, "Track")
    grid = at.dataframe[1].value  # [0] is the profile
    assert len(grid) == 25 and grid["TrackId"].iloc[0] == 1
    assert "Rows 1–25 of 3,503 · page 1 of 141" in [c.value for c in at.caption]
    at.button(key="rows:next").click().run()
    assert at.dataframe[1].value["TrackId"].iloc[0] == 26
    at.selectbox(key="rows:limit").set_value(50).run()  # changing size goes back to page 1
    assert len(at.dataframe[1].value) == 50 and at.dataframe[1].value["TrackId"].iloc[0] == 1


def test_data_tab_filters_sorts_and_reports_empty(ui, chinook):
    at = _open(ui, chinook, "Track")
    at.selectbox(key="rows:column").set_value("Composer")
    at.text_input(key="rows:value").input("AC/DC")
    at.button(key="rows:apply").click().run()
    assert "Rows 1–8 of 8 · page 1 of 1" in [c.value for c in at.caption]
    at.text_input(key="rows:value").input("no such composer")
    at.button(key="rows:apply").click().run()
    assert "No rows match." in [c.value for c in at.caption]


def test_data_tab_masks_and_hides_masked_filters(ui, chinook):
    at = _open(ui, chinook, "Customer")
    assert set(at.dataframe[1].value["Email"]) == {"***"}
    assert "Email" not in at.selectbox(key="rows:column").options


def test_explorer_data_tab_hides_masked_columns_without_profile(ui, client, chinook):
    client.put(f"/api/databases/{chinook}/masking", json={"columns": [{"table": "Customer", "column": "City"}]})
    at = ui("views/explorer.py", database_id=chinook)
    at.radio(key="explorer_table").set_value("Customer").run()
    for key in ("rows:column", "rows:sort"):
        options = at.selectbox(key=key).options
        assert "City" not in options and "Email" not in options and "Country" in options


def test_explorer_chip_clears_sidebar_filter(ui, chinook):
    at = ui("views/explorer.py", database_id=chinook)
    at.text_input(key="explorer_filter").set_value("cust").run()
    assert at.radio(key="explorer_table").value == "Customer"
    at.button(key="open:Employee").click().run()
    assert at.radio(key="explorer_table").value == "Employee"


def _run_sql(at, sql):
    at.text_area(key="sql:text").input(sql)
    at.button(key="sql:run").click().run()
    return at


def test_sql_tab_runs_query_with_masking(ui, chinook):
    at = _run_sql(_open(ui, chinook, "Customer"), "SELECT CustomerId, Email FROM Customer ORDER BY CustomerId")
    assert not at.exception and at.dataframe[-1].value.iloc[0].tolist() == [1, "***"]
    assert any(c.value == "59 rows · masked: Email" for c in at.caption)


def test_sql_tab_shows_refusal(ui, chinook):
    at = _run_sql(_open(ui, chinook, "Track"), "DELETE FROM Track")
    assert at.error[0].value == "only SELECT queries are allowed"


def test_sql_tab_handles_duplicate_column_names(ui, chinook):
    at = _run_sql(_open(ui, chinook, "Album"), "SELECT * FROM Album JOIN Artist ON Album.ArtistId = Artist.ArtistId")
    assert not at.exception
    assert list(at.dataframe[-1].value.columns) == ["AlbumId", "Title", "ArtistId", "ArtistId (2)", "Name"]


def test_sql_tab_reports_the_row_limit(ui, client, chinook):
    client.put("/api/settings", json={"max_rows": 5, "timeout_s": 5})
    at = _run_sql(_open(ui, chinook, "Track"), "SELECT Name FROM Track")
    assert any(c.value == "5 rows · stopped at the row limit (Management · Settings)" for c in at.caption)


def test_sql_tab_shows_headers_for_zero_rows(ui, chinook):
    at = _run_sql(_open(ui, chinook, "Track"), "SELECT Name FROM Track WHERE 0")
    assert not at.exception and list(at.dataframe[-1].value.columns) == ["Name"]
    assert any(c.value == "0 rows" for c in at.caption)
