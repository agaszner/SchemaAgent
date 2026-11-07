def test_settings_tab_saves_limits(ui, client):
    at = ui("views/management.py", tab="Settings")
    assert not at.exception and at.number_input(key="settings:max_rows").value == 100
    at.number_input(key="settings:max_rows").set_value(20)
    at.number_input(key="settings:timeout_s").set_value(1.5)
    at.button(key="settings:save").click().run()
    s = client.get("/api/settings").json()
    assert (s["max_rows"], s["timeout_s"]) == (20, 1.5)


def test_settings_tab_saves_llm(ui, client):
    at = ui("views/management.py", tab="Settings")
    assert at.text_input(key="settings:llm_model").value == "gpt-6-luna"
    assert at.selectbox(key="settings:reasoning_effort").value == "low"
    assert at.number_input(key="settings:input_price").value == 0.10
    assert at.number_input(key="settings:output_price").value == 0.50
    at.text_input(key="settings:llm_model").set_value("gpt-6-sol")
    at.button(key="settings:save").click().run()
    assert client.get("/api/settings").json()["llm_model"] == "gpt-6-sol"
