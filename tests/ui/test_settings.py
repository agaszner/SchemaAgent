def test_settings_tab_saves_limits(ui, client):
    at = ui("views/management.py", tab="Settings")
    assert not at.exception and at.number_input(key="settings:max_rows").value == 100
    at.number_input(key="settings:max_rows").set_value(20)
    at.number_input(key="settings:timeout_s").set_value(1.5)
    at.button(key="settings:save").click().run()
    assert client.get("/api/settings").json() == {"max_rows": 20, "timeout_s": 1.5}
