import json

import pytest

from core.agent import MAX_ROUNDS, answer_question
from core.graph import add_relation, build_graph
from core.parser import parse_database
from core.profiler import profile_database

DB = "data/chinook.db"
MODEL = parse_database(DB)
PROFILE = profile_database(DB, MODEL)
LLM = {"model": "m", "effort": "low", "max_rows": 100, "timeout_s": 5.0,
       "input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.50}


def sources(graph=None, relationships=()):
    return {"model": MODEL, "profile": PROFILE, "docs": {}, "relationships": list(relationships),
            "graph": graph if graph is not None else build_graph(MODEL)}


def ask(fake, question="q", src=None, **kw):
    return answer_question(fake, question, src or sources(), **{"history": [], "db_path": DB, "marks": [],
                                                                 "llm": LLM, **kw})


@pytest.fixture
def fake(fake_llm):
    return fake_llm


def test_schema_answer_uses_doc_and_path(fake):
    fake.script = [[("get_table_doc", {"table": "Invoice"}), ("find_path", {"from_table": "Customer", "to_table": "Track"})],
                   {"kind": "schema", "answer": "Customer → Invoice → InvoiceLine → Track.",
                    "tables": ["Customer", "Invoice", "InvoiceLine", "Track"], "columns": ["Invoice.CustomerId", "Nope.Col"]}]
    turn = ask(fake, "How do I get from customers to tracks?")
    assert turn["kind"] == "schema"
    assert {t for s in turn["path"] for t in (s["from_table"], s["to_table"])} == {"Customer", "Invoice", "InvoiceLine", "Track"}
    assert all(s["status"] == "accepted" for s in turn["path"]) and turn["assumed"] == []
    assert [c["name"] for c in turn["tool_calls"]] == ["get_table_doc", "find_path"]
    assert turn["tool_calls"][0]["output"].startswith("# Invoice\n") and "## Profile" in turn["tool_calls"][0]["output"]
    assert turn["unverified"] == ["Nope.Col"]
    assert turn["input_tokens"] == 200 and turn["cost_usd"] > 0
    assert turn["sql"] is None and turn["result"] is None


def test_data_answer_takes_sql_and_rows_from_the_trace(fake):
    fake.script = [[("run_sql", {"sql": "SELECT COUNT(*) FROM Customer WHERE Country = 'Germany'"})],
                   {"kind": "data", "answer": "4 customers.", "tables": ["Customer"], "columns": ["n"]}]
    turn = ask(fake, "How many customers in Germany?")
    assert turn["kind"] == "data" and turn["result"]["rows"] == [[4]] and "LIMIT" in turn["sql"]
    assert turn["unverified"] == []  # data answers name SQL aliases; only schema answers are checked


def test_failed_sql_is_repaired_once(fake):
    fake.script = [[("run_sql", {"sql": "SELECT Nme FROM Genre"})], [("run_sql", {"sql": "SELECT Name FROM Genre"})],
                   {"kind": "data", "answer": "Genres.", "tables": ["Genre"], "columns": []}]
    turn = ask(fake, "List the genres")
    assert turn["tool_calls"][0]["output"].startswith("SELECT Nme FROM Genre\n")  # "{sql}\n{kind}: {message}"
    assert turn["kind"] == "data" and len(turn["result"]["rows"]) == 25


def test_second_failure_disables_run_sql(fake):
    bad = [("run_sql", {"sql": "SELECT Nme FROM Genre"})]
    fake.script = [bad, bad, bad, {"kind": "data", "answer": "x", "tables": [], "columns": []}]
    turn = ask(fake, "List the genres")
    assert turn["tool_calls"][1]["output"].startswith("SELECT Nme FROM Genre\n")
    assert "no longer available" in turn["tool_calls"][2]["output"]
    assert turn["kind"] == "failed"  # data answer without a successful query
    assert turn["answer"] == "No query ran successfully, so this answer is unverified: x"


def test_database_failure_is_not_retried(fake, tmp_path):
    fake.script = [[("run_sql", {"sql": "SELECT 1"})], [("run_sql", {"sql": "SELECT 1"})],
                   {"kind": "abstain", "answer": "The database is gone.", "tables": [], "columns": []}]
    turn = ask(fake, "Anything?", db_path=tmp_path / "gone.db")
    assert "database:" in turn["tool_calls"][0]["output"] and "no longer available" in turn["tool_calls"][1]["output"]
    assert turn["kind"] == "abstain"


def test_round_cap_marks_failed(fake):
    fake.script = [[("search_tables", {"query": "x"})]] * MAX_ROUNDS
    turn = ask(fake)
    assert turn["kind"] == "failed" and len(fake.calls) == MAX_ROUNDS


def test_unknown_table_is_a_tool_message(fake):
    fake.script = [[("get_table_doc", {"table": "Nope"}), ("search_tables", {"query": "music genre"})],
                   {"kind": "abstain", "answer": "x", "tables": [], "columns": []}]
    turn = ask(fake)
    assert turn["tool_calls"][0]["output"].startswith("unknown table Nope; tables: Album, Artist")
    assert [h["table"] for h in json.loads(turn["tool_calls"][1]["output"])] == ["Genre", "Track"]


def test_malformed_calls_are_tool_messages(fake):
    fake.script = [[("run_sql", "{not json"), ("get_table_doc", {"tabel": "Genre"}), ("drop_table", {})],
                   {"kind": "abstain", "answer": "x", "tables": [], "columns": []}]
    turn = ask(fake)
    outputs = [c["output"] for c in turn["tool_calls"]]
    assert turn["tool_calls"][0]["arguments"] == "{not json" and outputs[0].startswith("Expecting property name")
    assert "unexpected keyword argument 'tabel'" in outputs[1]
    assert outputs[2] == "unknown tool drop_table; tools: search_tables, get_table_doc, find_path, get_sample_rows, run_sql"


def test_missing_parsed_answer_is_a_refusal(fake):
    fake.script = [None]
    turn = ask(fake, "Something the model refuses")
    assert (turn["kind"], turn["answer"]) == ("refuse", "The model refused to answer.")


def test_rejected_edge_never_reaches_the_model(fake):
    graph = build_graph(MODEL)
    for edge in graph["Invoice"]["Customer"].values():
        edge["status"] = "rejected"
    rejected = {"from_table": "Invoice", "from_columns": ["CustomerId"], "to_table": "Customer", "to_columns": ["CustomerId"],
                "source": "declared", "status": "rejected", "confidence": 1.0}
    fake.script = [[("find_path", {"from_table": "Invoice", "to_table": "Customer"}), ("get_table_doc", {"table": "Invoice"})],
                   {"kind": "abstain", "answer": "no path", "tables": [], "columns": []}]
    turn = ask(fake, "How do invoices relate to customers?", sources(graph, [rejected]))
    assert all(s["status"] != "rejected" for s in turn["path"])
    assert turn["tool_calls"][0]["output"] == "no path between Invoice and Customer"
    assert "Invoice(CustomerId) → Customer" not in turn["tool_calls"][1]["output"]


def test_proposed_step_is_assumed(fake):
    graph = build_graph(MODEL)
    graph.remove_edge("Track", "Genre")
    add_relation(graph, {"table": "Track", "columns": ["GenreId"], "ref_table": "Genre", "ref_columns": ["GenreId"],
                         "confidence": 0.9}, source="inferred", status="proposed")
    fake.script = [[("find_path", {"from_table": "track", "to_table": "Genre"})],
                   {"kind": "schema", "answer": "Via GenreId (assumed).", "tables": ["Track", "Genre"], "columns": []}]
    turn = ask(fake, "How do tracks link to genres?", sources(graph))
    assert turn["assumed"] == ["Track.GenreId → Genre.GenreId"]


def test_no_raw_email_reaches_the_model(fake):
    fake.script = [[("run_sql", {"sql": "SELECT Email FROM Customer WHERE CustomerId = 1"}),
                    ("get_sample_rows", {"table": "Customer", "limit": 5}), ("get_table_doc", {"table": "Customer"})],
                   {"kind": "data", "answer": "masked", "tables": ["Customer"], "columns": ["Customer.Email"]}]
    turn = ask(fake, "What is the email of customer 1?")
    assert turn["result"]["rows"] == [["***"]]
    assert len(json.loads(turn["tool_calls"][1]["output"])) == 5
    assert "luisg@embraer.com.br" not in json.dumps([c["input"] for c in fake.calls], default=str)


def test_marked_column_is_masked_in_samples(fake):
    fake.script = [[("get_sample_rows", {"table": "Customer", "limit": 99})],
                   {"kind": "data", "answer": "x", "tables": [], "columns": []}]
    turn = ask(fake, marks=[("Customer", "LastName")])
    rows = json.loads(turn["tool_calls"][0]["output"])
    assert len(rows) == 10 and {r["LastName"] for r in rows} == {"***"}


def test_write_request_is_refused_without_tools(fake):
    fake.script = [{"kind": "refuse", "answer": "I only read data.", "tables": [], "columns": []}]
    turn = ask(fake, "Delete all invoices")
    assert turn["kind"] == "refuse" and turn["tool_calls"] == []


def test_history_goes_first(fake):
    fake.script = [{"kind": "schema", "answer": "ok", "tables": [], "columns": []}]
    ask(fake, "and its columns?", history=[{"question": "Where are invoices?", "answer": "In Invoice."}])
    first = fake.calls[0]["input"]
    assert first[0] == {"role": "user", "content": "Where are invoices?"} and first[-1]["content"] == "and its columns?"
    assert first[1] == {"role": "assistant", "content": "In Invoice."}


def test_proposed_relationship_in_sql_is_assumed(fake):
    proposed = {"from_table": "Track", "from_columns": ["GenreId"], "to_table": "Genre", "to_columns": ["GenreId"],
                "source": "inferred", "status": "proposed", "confidence": 0.9}
    sql = "SELECT g.Name, COUNT(*) FROM track t JOIN Genre g ON t.GenreId = g.GenreId GROUP BY g.Name"
    fake.script = [[("run_sql", {"sql": sql})], {"kind": "data", "answer": "x", "tables": [], "columns": []}]
    turn = ask(fake, "Tracks per genre?", sources(relationships=[proposed]))
    assert turn["path"] == [] and turn["assumed"] == ["Track.GenreId → Genre.GenreId"]
