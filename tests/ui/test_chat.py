from ui import api_client

COUNT = [("run_sql", {"sql": "SELECT COUNT(*) AS n FROM Customer"})]


def _reply(kind, answer="x"):
    return {"kind": kind, "answer": answer, "tables": [], "columns": []}


def _ask(ui, db, question="How many customers?"):
    at = ui("views/chat.py", db)
    at.chat_input[0].set_value(question).run()
    assert not at.exception
    return at


def test_question_shows_answer_sql_and_table(ui, chinook, fake_llm):
    fake_llm.script = [COUNT, {"kind": "data", "answer": "59 customers.", "tables": ["Customer"], "columns": []}]
    at = _ask(ui, chinook)
    assert any("59 customers." in m.value for m in at.markdown)
    assert at.code and "COUNT(*)" in at.code[0].value and at.dataframe
    assert at.dataframe[0].value.to_dict("records") == [{"n": 59}]
    assert any("s ·" in c.value and "$" in c.value for c in at.caption)  # latency and cost
    assert any("240 tokens" in c.value for c in at.caption)  # two rounds of 100 + 20


def test_duplicate_result_columns_render(ui, chinook, fake_llm):
    sql = "SELECT a.Name, g.Name FROM Artist a JOIN Genre g ON a.ArtistId = g.GenreId LIMIT 2"
    fake_llm.script = [[("run_sql", {"sql": sql})], _reply("data")]
    at = _ask(ui, chinook)
    assert list(at.dataframe[0].value.columns) == ["Name", "Name (2)"]


def test_refusal_is_flagged(ui, chinook, fake_llm):
    fake_llm.script = [_reply("refuse", "I will not do that.")]
    at = _ask(ui, chinook, "Drop the Customer table")
    assert any("Refused" in w.value for w in at.warning)
    assert not at.expander  # no tool calls, tables or path: nothing to show as evidence


def test_abstain_is_flagged(ui, chinook, fake_llm):
    fake_llm.script = [_reply("abstain", "The database has no weather data.")]
    at = _ask(ui, chinook, "What was the weather?")
    assert any("Not enough information" in i.value for i in at.info)


def test_assumed_relationship_is_flagged(ui, shop, fake_llm):
    # shop has the proposed edge Orders.CustomerId → Customer.CustomerId (chinook has none)
    fake_llm.script = [[("find_path", {"from_table": "Orders", "to_table": "Customer"})],
                       {"kind": "schema", "answer": "Via CustomerId.", "tables": ["Orders", "Customer"], "columns": []}]
    at = _ask(ui, shop, "How do orders link to customers?")
    assert any("Orders.CustomerId → Customer.CustomerId" in w.value for w in at.warning)
    assert any("Orders.CustomerId → Customer.CustomerId (assumed)" in m.value for m in at.markdown)


def test_history_survives_reload(ui, chinook, fake_llm):
    fake_llm.script = [COUNT, {"kind": "data", "answer": "59 customers.", "tables": ["Customer"], "columns": []}]
    api_client.request("POST", f"/databases/{chinook}/chat", json={"question": "How many customers?"})
    at = ui("views/chat.py", chinook)
    texts = [m.value for m in at.markdown]
    assert "How many customers?" in texts and "59 customers." in texts
    assert "COUNT(*)" in at.code[0].value and at.dataframe[0].value.to_dict("records") == [{"n": 59}]
    assert [e.label for e in at.expander] == ["Evidence"]
    assert "Tables: Customer" in [m.value for m in at.expander[0].markdown]


def test_dollar_amounts_are_not_math(ui, chinook, fake_llm):
    fake_llm.script = [_reply("abstain", "2009 revenue was $449.46 and 2010 was $481.45")]
    at = _ask(ui, chinook, "Was $449.46 more than $481.45?")
    texts = [m.value for m in at.markdown]
    assert r"2009 revenue was \$449.46 and 2010 was \$481.45" in texts
    assert r"Was \$449.46 more than \$481.45?" in texts


def test_sql_shows_for_any_kind_that_ran_it(ui, chinook, fake_llm):
    fake_llm.script = [COUNT, {"kind": "schema", "answer": "Customer holds them.", "tables": ["Customer"], "columns": []}]
    at = _ask(ui, chinook)
    assert "COUNT(*)" in at.code[0].value and at.dataframe


def test_feedback_buttons_store(ui, chinook, fake_llm, client):
    fake_llm.script = [_reply("abstain")]
    at = _ask(ui, chinook)
    msg = client.get(f"/api/databases/{chinook}/chat").json()[0]["id"]
    at.button(key=f"chat:{msg}:up").click().run()
    assert client.get(f"/api/databases/{chinook}/chat").json()[0]["feedback"] == "up"
    assert at.button(key=f"chat:{msg}:up").proto.type == "primary"
    at.button(key=f"chat:{msg}:up").click().run()  # a second click clears it
    assert client.get(f"/api/databases/{chinook}/chat").json()[0]["feedback"] is None


def test_before_pipeline_shows_hint(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    at = ui("views/chat.py", db)
    assert not at.exception
    assert at.info[0].value.startswith("Run the pipeline first")
    assert not at.chat_input


def test_parsed_only_shows_hint_on_ask(ui, client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "parse"})
    at = _ask(ui, db)
    assert any(i.value.startswith("Run the pipeline first") for i in at.info) and not at.error


def test_api_error_is_shown(ui, chinook):  # 422: the API caps questions at 2000 characters
    at = _ask(ui, chinook, "x" * 2001)
    assert at.error and "2000" in at.error[0].value
