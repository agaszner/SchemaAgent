from types import SimpleNamespace

import pytest

from core.evaluation import load_gold, load_questions, results_match, run_question, summarize
from core.graph import build_graph
from core.parser import parse_database

DB = "data/chinook.db"
MODEL = parse_database(DB)
SOURCES = {"ddl": "", "model": MODEL, "docs": {}, "relationships": [], "profile": {"tables": []}, "graph": build_graph(MODEL)}
Q = {q["id"]: q for q in load_questions("benchmark/questions.jsonl")}
GOLD = load_gold("benchmark/gold.sql")
LLM = {"model": "m", "effort": "low", "max_rows": 100, "timeout_s": 5.0,
       "input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.50}


class ScriptedFake:
    """responses.parse returns the next scripted reply, validated by text_format, with 100/20 tokens."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = kwargs["text_format"].model_validate(self.replies.pop(0))
        return SimpleNamespace(output_parsed=parsed, usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def sql_reply(sql, tables=(), columns=()):
    return {"kind": "sql", "sql": sql, "answer": "a", "tables": list(tables), "columns": list(columns), "path": []}


def reply(kind, tables=(), columns=(), path=()):
    return {"kind": kind, "sql": None, "answer": "a", "tables": list(tables), "columns": list(columns), "path": list(path)}


def run(fake, qid, config="B", **kw):
    return run_question(fake, config, Q[qid], GOLD.get(qid), SOURCES, **{"db_path": DB, "marks": [], "llm": LLM, **kw})


def test_results_match_ignores_order_names_extra_columns_and_float_noise():
    assert results_match([["a", 1.0], ["b", 2.0]], [[2.0000001, "b", 9], [1.0, "a", 9]])
    assert not results_match([[1]], [[1], [1]])        # row count differs
    assert not results_match([["Rock"]], [["rock"]])   # values compare exactly
    assert results_match([[None, 1], ["x", 2]], [["x", 2.0], [None, 1]])  # mixed types sort; 2 == 2.0


def test_sql_answer_scored_against_gold():
    fake = ScriptedFake([sql_reply("SELECT count(*) FROM Customer WHERE Country = 'Germany'")])
    row = run(fake, "d01")
    assert (row["correct"], row["first_valid"], row["repaired"], len(fake.calls)) == (True, True, None, 1)
    assert list(row) == ["config", "id", "category", "kind", "answer_kind", "correct", "score", "path_correct",
                         "first_valid", "repaired", "claims", "unsupported", "latency_s", "input_tokens",
                         "output_tokens", "cost_usd", "answer", "tables", "columns", "path", "first_sql", "sql",
                         "error"]
    assert (row["config"], row["id"], row["answer_kind"], row["input_tokens"], row["output_tokens"]) == \
        ("B", "d01", "sql", 100, 20)
    assert row["cost_usd"] == pytest.approx((100 * 0.10 + 20 * 0.50) / 1e6)
    assert row["error"] is None and row["score"] is None and row["path_correct"] is None
    assert row["first_sql"] == row["sql"] == "SELECT count(*) FROM Customer WHERE Country = 'Germany'"
    assert (row["answer"], row["tables"], row["columns"], row["path"]) == ("a", "", "", "")


def test_answer_lists_are_joined_for_the_csv():
    path = ["Customer", "Invoice", "InvoiceLine", "Track"]
    row = run(ScriptedFake([reply("schema", path, ["Invoice.CustomerId", "Customer.CustomerId"], path)]), "s11")
    assert row["tables"] == row["path"] == "Customer; Invoice; InvoiceLine; Track"
    assert row["columns"] == "Invoice.CustomerId; Customer.CustomerId"
    assert (row["first_sql"], row["sql"]) == (None, None)


def test_failed_sql_then_abstain_keeps_the_first_sql():
    bad = "SELECT count(*) FROM Customers WHERE Country = 'Germany'"
    abstain = {**reply("abstain"), "answer": "Not sure."}
    row = run(ScriptedFake([sql_reply(bad), abstain]), "d01")
    assert (row["first_sql"], row["sql"], row["answer"], row["answer_kind"]) == (bad, None, "Not sure.", "abstain")
    assert (row["first_valid"], row["repaired"], row["correct"]) == (False, False, False)


def test_call_shape():
    fake = ScriptedFake([sql_reply("SELECT 1")])
    run(fake, "d01", config="A")
    call = fake.calls[0]
    assert (call["model"], call["reasoning"]) == ("m", {"effort": "low"})
    assert call["instructions"].startswith("You answer questions about one SQLite database")
    assert call["input"].endswith("\n\n## Question\n" + Q["d01"]["question"])
    assert call["input"].startswith("## Schema (DDL)")


def test_invalid_sql_gets_one_repair_with_the_error():
    fake = ScriptedFake([sql_reply("SELECT count(*) FROM Customers WHERE Country = 'Germany'"),
                         sql_reply("SELECT count(*) FROM Customer WHERE Country = 'Germany'")])
    row = run(fake, "d01", config="A")
    assert (row["correct"], row["first_valid"], row["repaired"]) == (True, False, True)
    assert "unknown table" in fake.calls[1]["input"] and "Customers" in fake.calls[1]["input"]
    assert "## Your previous SQL failed\nSELECT count(*) FROM Customers" in fake.calls[1]["input"]
    assert (row["input_tokens"], row["output_tokens"], row["error"]) == (200, 40, None)
    assert row["sql"] == "SELECT count(*) FROM Customer WHERE Country = 'Germany'"


def test_masked_filter_is_refused_and_repair_can_fail():
    bad = sql_reply("SELECT count(*) FROM Customer WHERE Email LIKE '%gmail%'")
    fake = ScriptedFake([bad, bad])
    row = run(fake, "d01")
    assert (row["correct"], row["first_valid"], row["repaired"], len(fake.calls)) == (False, False, False, 2)
    assert row["error"].startswith("invalid: column Customer.Email is masked")


def test_sql_kind_without_sql_is_repaired():
    fake = ScriptedFake([{**sql_reply(None)}, sql_reply("SELECT count(*) FROM Customer WHERE Country = 'Germany'")])
    row = run(fake, "d01")
    assert (row["correct"], row["first_valid"], row["repaired"]) == (True, False, True)


def test_database_error_is_not_retried(tmp_path):
    fake = ScriptedFake([sql_reply("SELECT count(*) FROM Customer WHERE Country = 'Germany'")])
    row = run(fake, "d01", db_path=tmp_path / "missing.db")
    assert (row["correct"], row["first_valid"], row["repaired"], len(fake.calls)) == (False, False, None, 1)
    assert row["error"].startswith("database: ")


def test_abstain_is_right_only_for_unanswerable():
    row = run(ScriptedFake([reply("abstain")]), "d16")
    assert (row["correct"], row["first_valid"], row["answer_kind"]) == (True, None, "abstain")
    row = run(ScriptedFake([reply("abstain")]), "d01")
    assert (row["correct"], row["first_valid"], row["repaired"]) == (False, None, None)


def test_unanswerable_data_question_answered_with_sql_is_wrong():
    row = run(ScriptedFake([sql_reply("SELECT count(*) FROM Track")]), "d16")
    assert (row["correct"], row["first_valid"]) == (False, True)


def test_schema_scoring():
    row = run(ScriptedFake([reply("schema", ["Customer"], ["customer.email"])]), "s01")
    assert (row["score"], row["correct"], row["path_correct"], row["first_valid"]) == (1.0, True, None, None)

    path = ["Track", "InvoiceLine", "Invoice", "Customer"]
    row = run(ScriptedFake([reply("schema", path, path=path)]), "s11")
    assert (row["path_correct"], row["score"], row["correct"]) == (True, 1.0, True)

    row = run(ScriptedFake([reply("schema", ["Customer", "Invoice"], path=["Customer", "Track"])]), "s11")
    assert (row["path_correct"], row["score"], row["correct"]) == (False, 0.5, False)

    row = run(ScriptedFake([reply("schema", ["Customer"], ["Customer.EMail", "Customer.Nope", "Ghost.Id"])]), "s01")
    assert (row["claims"], row["unsupported"], row["score"]) == (4, 2, 1.0)

    row = run(ScriptedFake([reply("schema", ["Nowhere"], ["Email"])]), "s01")
    assert (row["claims"], row["unsupported"]) == (2, 1)  # a bare column exists if any table has it

    row = run(ScriptedFake([reply("schema", ["InvoiceLine"], ["il.TrackId"])]), "s09")
    assert (row["claims"], row["unsupported"]) == (2, 0)  # an alias prefix falls back to the bare column

    row = run(ScriptedFake([reply("abstain")]), "s16")
    assert (row["correct"], row["score"], row["path_correct"]) == (True, None, None)
    assert run(ScriptedFake([reply("schema", ["Invoice"])]), "s16")["correct"] is False


def test_claims_count_only_schema_answers():
    sql = "SELECT count(*) AS tracks_listened_to FROM Track"
    row = run(ScriptedFake([sql_reply(sql, ["Track"], ["COUNT(*)", "tracks_listened_to"])]), "d16")
    assert (row["claims"], row["unsupported"]) == (0, 0)


def _row(config, category, kind, answer_kind, **kw):
    base = {"config": config, "id": "x", "category": category, "kind": kind, "answer_kind": answer_kind,
            "correct": False, "score": None, "path_correct": None, "first_valid": None, "repaired": None,
            "claims": 0, "unsupported": 0, "latency_s": 1.0, "input_tokens": 100, "output_tokens": 20,
            "cost_usd": 0.5, "answer": "a", "tables": "", "columns": "", "path": "", "first_sql": None,
            "sql": None, "error": None}
    return {**base, **kw}


def test_summarize():
    rows = [
        _row("A", "data", "simple", "sql", correct=True, first_valid=True, latency_s=1.0),
        _row("A", "data", "join", "sql", first_valid=False, repaired=True, correct=True, latency_s=3.0),
        _row("A", "data", "value", "abstain", claims=2, unsupported=1),
        _row("A", "data", "unanswerable", "abstain", correct=True),
        _row("A", "data", "unanswerable", "abstain", first_valid=False, repaired=False, correct=True),  # not counted
        _row("A", "schema", "columns", "schema", score=1.0, correct=True, claims=2),
        _row("A", "schema", "path", "schema", score=0.5, path_correct=False, claims=4, unsupported=1),
        _row("B", "schema", "unanswerable", "schema", cost_usd=0.25),
    ]
    out = summarize(rows)
    assert set(out) == {"A", "B"}
    a = out["A"]
    assert a["n"] == 7
    assert a["execution_accuracy"] == pytest.approx(2 / 3)
    assert a["valid_sql_rate"] == pytest.approx(1 / 3)
    assert a["repair_success"] == 1.0
    assert a["schema_fact_accuracy"] == 0.75
    assert a["path_accuracy"] == 0.0
    assert a["unsupported_claim_rate"] == pytest.approx(2 / 8)
    assert a["abstention_accuracy"] == pytest.approx(6 / 7)
    assert a["mean_latency_s"] == pytest.approx(9 / 7)
    assert (a["input_tokens"], a["output_tokens"], a["cost_usd"]) == (700, 140, pytest.approx(3.5))
    b = out["B"]
    assert b["n"] == 1 and b["abstention_accuracy"] == 0.0 and b["cost_usd"] == 0.25
    for key in ("execution_accuracy", "valid_sql_rate", "repair_success", "schema_fact_accuracy",
                "path_accuracy", "unsupported_claim_rate"):
        assert b[key] is None
