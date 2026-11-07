import csv
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from api import llm
from api.appdb import connect
from api.config import settings
from core.evaluation import load_gold, load_questions

PICK = ("d01", "s01", "d16")  # simple data, schema columns, unanswerable
QUESTIONS = {q["id"]: q for q in load_questions("benchmark/questions.jsonl")}
GOLD = load_gold("benchmark/gold.sql")
D01 = GOLD["d01"]
ANSWER = {"kind": "sql", "sql": D01, "answer": "a", "tables": [], "columns": [], "path": []}


def write_benchmark(gold: str = D01, pick=PICK, extra_gold: str = "") -> None:
    folder = settings.benchmark_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "questions.jsonl").write_text("".join(json.dumps(QUESTIONS[i]) + "\n" for i in pick), encoding="utf-8")
    (folder / "gold.sql").write_text(f"-- d01\n{gold};\n{extra_gold}", encoding="utf-8")


def start(client, db):
    return client.post(f"/api/databases/{db}/benchmark")


def test_benchmark_runs_every_question_in_every_config(client, chinook, fake_llm):
    write_benchmark()
    fake_llm.reply = ANSWER
    before = len(fake_llm.calls)
    response = start(client, chinook)
    assert response.status_code == 202 and response.json()["step"] == "benchmark"
    out = client.get(f"/api/databases/{chinook}/benchmark").json()
    assert (out["run"]["status"], out["run"]["done"], out["run"]["total"]) == ("done", 9, 9)
    assert out["summary"]["A"]["execution_accuracy"] == 1.0
    assert len(out["rows"]) == 9
    with open(settings.benchmark_dir / "results.csv", newline="", encoding="utf-8") as f:
        lines = list(csv.reader(f))
    assert ",".join(lines[0]).startswith("config,id,category,kind,") and len(lines) == 10
    assert ",".join(lines[0]).endswith(",cost_usd,answer,tables,columns,path,first_sql,sql,error")
    calls = fake_llm.calls[before:]
    assert len(calls) == 9
    a, b, c = calls[:3], calls[3:6], calls[6:]
    assert all(x["input"].startswith("## Schema (DDL)") for x in a)
    assert all(x["input"].startswith("## Tables") for x in b + c)
    assert [("## Data profile" in x["input"]) for x in calls] == [False] * 6 + [True] * 3


def test_get_without_a_benchmark_run_is_null(client, chinook):
    assert client.get(f"/api/databases/{chinook}/benchmark").json() is None


def test_needs_questions_for_the_database(client, chinook):
    response = start(client, chinook)
    assert response.status_code == 409 and response.json()["detail"] == "no benchmark questions for chinook"


def test_needs_every_doc(client, chinook):
    write_benchmark()
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM table_docs WHERE database_id = ? AND table_name = 'Album'", (chinook,))
    response = start(client, chinook)
    assert response.status_code == 409 and response.json()["detail"] == "run docs first"


def test_needs_the_pipeline_first(client, tmp_path):
    write_benchmark()
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    response = start(client, db)
    assert response.status_code == 409 and response.json()["detail"] == "run parse first"


def test_conflicts_with_an_active_run(client, chinook):
    write_benchmark()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (chinook,))
    response = start(client, chinook)
    assert response.status_code == 409 and response.json()["detail"] == "a pipeline run is already in progress"


def test_all_does_not_include_the_benchmark(client, chinook):
    runs = client.post(f"/api/databases/{chinook}/runs", json={"step": "all"}).json()
    assert [r["step"] for r in runs] == ["parse", "profile", "graph", "docs"]


def test_holds_no_lock_during_the_llm_call(client, chinook, fake_llm, monkeypatch):
    class EditingFake(type(fake_llm)):
        def parse(self, **kwargs):  # a relationship edit while the model answers must not hit "database is locked"
            with closing(sqlite3.connect(settings.app_db, timeout=0)) as conn, conn:
                conn.execute("UPDATE relationships SET note = 'edited' WHERE database_id = ?", (chinook,))
            return super().parse(**kwargs)

    editing = EditingFake()
    editing.reply = ANSWER
    monkeypatch.setattr(llm, "client", lambda: editing)
    write_benchmark()
    start(client, chinook)
    run = client.get(f"/api/databases/{chinook}/benchmark").json()["run"]
    assert (run["status"], run["error"], run["done"]) == ("done", None, 9)


def test_a_refusal_is_a_wrong_row_not_a_failed_run(client, chinook, fake_llm, monkeypatch):
    class RefusingFake(type(fake_llm)):
        def parse(self, **kwargs):
            if kwargs["input"].endswith(QUESTIONS["s01"]["question"]):
                self.calls.append(kwargs)
                return SimpleNamespace(output_parsed=None, usage=SimpleNamespace(input_tokens=100, output_tokens=0))
            return super().parse(**kwargs)

    refusing = RefusingFake()
    refusing.reply = ANSWER
    monkeypatch.setattr(llm, "client", lambda: refusing)
    write_benchmark()
    start(client, chinook)
    out = client.get(f"/api/databases/{chinook}/benchmark").json()
    assert out["run"]["status"] == "done" and len(out["rows"]) == 9
    refused = [r for r in out["rows"] if r["id"] == "s01"]
    assert [(r["correct"], r["error"]) for r in refused] == [(False, "refused")] * 3
    assert list(refused[0]) == list(out["rows"][0])  # same keys, same order: results.csv takes its header from row 0


def test_a_broken_gold_query_fails_the_run_before_any_llm_call(client, chinook, fake_llm):
    write_benchmark(gold="SELECT nope FROM Customer")
    fake_llm.reply = ANSWER
    before = len(fake_llm.calls)
    start(client, chinook)
    run = client.get(f"/api/databases/{chinook}/benchmark").json()["run"]
    assert run["status"] == "error" and "d01" in run["error"] and "nope" in run["error"]
    assert len(fake_llm.calls) == before
    assert not (settings.benchmark_dir / "results.csv").exists()


def test_a_gold_query_on_a_marked_column_fails_the_run_before_any_llm_call(client, chinook, fake_llm):
    mark = [{"table": "Customer", "column": "FirstName"}]
    assert client.put(f"/api/databases/{chinook}/masking", json={"columns": mark}).status_code == 200
    client.post(f"/api/databases/{chinook}/runs", json={"step": "all"})  # the mark drops the profile and docs
    write_benchmark(pick=PICK + ("d10",), extra_gold=f"-- d10\n{GOLD['d10']};\n")
    fake_llm.reply = ANSWER
    before = len(fake_llm.calls)
    assert start(client, chinook).status_code == 202
    run = client.get(f"/api/databases/{chinook}/benchmark").json()["run"]
    assert run["status"] == "error" and "d10" in run["error"] and "masked columns: FirstName" in run["error"]
    assert len(fake_llm.calls) == before
    assert not (settings.benchmark_dir / "results.csv").exists()
