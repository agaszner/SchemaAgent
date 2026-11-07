import json

from api.config import settings
from core.evaluation import load_gold, load_questions

PICK = ("d01", "s01", "d16")
D01 = load_gold("benchmark/gold.sql")["d01"]
ANSWER = {"kind": "sql", "sql": D01, "answer": "a", "tables": [], "columns": [], "path": []}


def _tab(ui, db):
    return ui("views/management.py", database_id=db, tab="Benchmark")


def _write_benchmark() -> None:
    folder = settings.benchmark_dir
    folder.mkdir(parents=True, exist_ok=True)
    questions = [q for q in load_questions("benchmark/questions.jsonl") if q["id"] in PICK]
    (folder / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    (folder / "gold.sql").write_text(f"-- d01\n{D01};\n", encoding="utf-8")


def test_before_any_run(ui, chinook):
    at = _tab(ui, chinook)
    assert not at.exception and at.info and not at.button(key="bench:run").disabled


def test_run_shows_the_summary(ui, chinook, fake_llm):
    _write_benchmark()
    fake_llm.reply = ANSWER
    at = _tab(ui, chinook)
    at.button(key="bench:run").click().run()
    assert not at.exception and not at.error
    summary = at.dataframe[0].value
    assert list(summary.columns) == ["A", "B", "C"]
    assert list(summary.loc["Execution accuracy"]) == ["100%", "100%", "100%"]
    assert summary.loc["Questions"].tolist() == ["3", "3", "3"]
    assert summary.loc["Path accuracy"].tolist() == ["—", "—", "—"]
    assert len(at.dataframe[1].value) == 9
    assert any("results.csv" in c.value for c in at.caption)


def test_no_questions_shows_the_conflict(ui, chinook):
    at = _tab(ui, chinook)
    at.button(key="bench:run").click().run()
    assert at.error[0].value == "no benchmark questions for chinook"


def test_disabled_while_a_run_is_active(ui, chinook):
    from contextlib import closing
    from api.appdb import connect
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (chinook,))
    assert _tab(ui, chinook).button(key="bench:run").disabled


def test_other_active_run_next_to_a_finished_benchmark(ui, chinook, fake_llm):
    from contextlib import closing
    from api.appdb import connect
    _write_benchmark()
    fake_llm.reply = ANSWER
    at = _tab(ui, chinook)
    at.button(key="bench:run").click().run()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO runs (database_id, step, status) VALUES (?, 'parse', 'running')", (chinook,))
    at.run()
    assert not at.exception and at.button(key="bench:run").disabled and at.dataframe
