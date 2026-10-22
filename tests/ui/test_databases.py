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
