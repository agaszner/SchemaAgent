def test_register_and_upload_from_databases_tab(ui, tmp_path):
    at = ui("views/management.py", tab="Databases")
    at.text_input(key="register_path").input(str(tmp_path / "chinook.db"))
    at.button(key="register").click().run()
    assert not at.error
    assert at.dataframe[0].value["Database"].tolist() == ["chinook"]
    at.text_input(key="register_path").input(str(tmp_path / "chinook.db"))
    at.button(key="register").click().run()
    assert at.error[0].value == "already registered"
    data = (tmp_path / "chinook.db").read_bytes()
    at.file_uploader(key="upload_file").upload("music.db", data, "application/octet-stream")
    at.button(key="upload").click().run()
    assert at.dataframe[0].value["Database"].tolist() == ["chinook", "music"]


def test_upload_shows_backend_rejection(ui):
    at = ui("views/management.py", tab="Databases")
    at.file_uploader(key="upload_file").upload("fake.db", b"hello", "application/octet-stream")
    at.button(key="upload").click().run()
    assert at.error[0].value == "not a SQLite database"


def test_remove_asks_for_confirmation(ui, client, chinook, shop):
    at = ui("views/management.py", tab="Databases", database_id=shop)
    at.selectbox(key="remove_target").set_value(shop).run()
    at.button(key="remove").click().run()
    assert at.warning[0].value.startswith("Removing shop deletes")
    at.button(key="remove_cancel").click().run()
    assert not at.warning and len(client.get("/api/databases").json()) == 2
    at.button(key="remove").click().run()
    at.button(key="remove_confirm").click().run()
    assert not at.exception
    assert [d["name"] for d in client.get("/api/databases").json()] == ["chinook"]
    assert at.sidebar.selectbox(key="database_id").value == chinook
