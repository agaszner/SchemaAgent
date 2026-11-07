import functools
import re
import time
from pathlib import Path

import pytest

from core.evaluation import build_context, load_gold, load_questions
from core.graph import build_graph, find_path
from core.parser import parse_database
from core.profiler import profile_database
from core.sql_guard import run_sql

FINANCIAL = "data/bird/financial/financial.sqlite"  # not in git: its parameters skip when it is missing
DATABASES = [
    pytest.param("chinook", "data/chinook.db", "", id="chinook"),
    pytest.param("financial", FINANCIAL, "f", id="financial",
                 marks=pytest.mark.skipif(not Path(FINANCIAL).exists(), reason="run data/download.sh hard")),
]
GOLD_IDS = [pytest.param(db.values[1], f"{db.values[2]}d{i:02}", id=f"{db.values[2]}d{i:02}", marks=db.marks)
            for db in DATABASES for i in range(1, 16)]


@functools.cache
def model(path: str) -> dict:
    return parse_database(path)


@functools.cache
def profile(path: str) -> dict:
    return profile_database(path, model(path))


def questions(name: str) -> list[dict]:
    return [q for q in load_questions("benchmark/questions.jsonl") if q["database"] == name]


@pytest.mark.parametrize("name, path, p", DATABASES)
def test_counts_and_ids(name, path, p):
    qs = questions(name)
    assert [q["id"] for q in qs] == [f"{p}s{i:02}" for i in range(1, 21)] + [f"{p}d{i:02}" for i in range(1, 21)]
    kinds = [q["kind"] for q in qs]
    assert kinds == [k for k in ("columns", "relationship", "path", "unanswerable") for _ in range(5)] + \
                    [k for k in ("simple", "join", "value", "unanswerable") for _ in range(5)]
    assert [q["category"] for q in qs] == ["schema"] * 20 + ["data"] * 20
    assert {q["database"] for q in qs} == {name}
    assert all(q["question"].strip() for q in qs)


@pytest.mark.parametrize("name, path, p", DATABASES)
def test_gold_covers_exactly_the_answerable_data_questions(name, path, p):
    gold = {k for k in load_gold("benchmark/gold.sql") if re.fullmatch(rf"{p}d\d{{2}}", k)}
    assert gold == {q["id"] for q in questions(name) if q["category"] == "data" and q["kind"] != "unanswerable"}
    assert gold == {f"{p}d{i:02}" for i in range(1, 16)}


def test_every_gold_block_belongs_to_an_answerable_data_question():
    """The per-database coverage test filters by id pattern; this one catches stray or mistyped blocks."""
    answerable = {q["id"] for q in load_questions("benchmark/questions.jsonl")
                  if q["category"] == "data" and q["kind"] != "unanswerable"}
    assert set(load_gold("benchmark/gold.sql")) <= answerable


@pytest.mark.parametrize("path, qid", GOLD_IDS)
def test_gold_runs_through_the_guard(path, qid):
    sql = load_gold("benchmark/gold.sql")[qid]
    db_model = model(path)
    start = time.perf_counter()
    out = run_sql(path, sql, db_model)
    assert time.perf_counter() - start < 2, qid
    assert 1 <= len(out["rows"]) <= 99 and not out["masked"]


@pytest.mark.parametrize("name, path, p", DATABASES)
def test_schema_expectations_exist_in_the_model(name, path, p):
    db_model = model(path)
    tables = {t["name"]: {c["name"] for c in t["columns"]} for t in db_model["tables"]}
    fks = {(t["name"], fk["ref_table"]) for t in db_model["tables"] for fk in t["foreign_keys"]}
    fk_columns = {(f"{t['name']}.{c}", f"{fk['ref_table']}.{r}") for t in db_model["tables"]
                  for fk in t["foreign_keys"] for c, r in zip(fk["columns"], fk["ref_columns"])}
    graph = build_graph(db_model)
    for q in questions(name):
        expected = q["expected"]
        if q["category"] == "data" or q["kind"] == "unanswerable":
            assert expected == {}, q["id"]
            continue
        keys = {"columns": {"tables", "columns"}, "relationship": {"tables", "columns", "path"},
                "path": {"tables", "path"}}[q["kind"]]
        assert set(expected) == keys, q["id"]
        assert expected["tables"] and set(expected["tables"]) <= set(tables), q["id"]
        for col in expected.get("columns", []):
            table, column = col.split(".")
            assert column in tables[table], col
        if q["kind"] == "relationship":  # child column -> parent column of a declared FK
            assert tuple(expected["columns"]) in fk_columns, q["id"]
        path = expected.get("path")
        if path:
            assert set(expected["tables"]) == set(path), q["id"]
            assert len(path) == 2 if q["kind"] == "relationship" else len(path) >= 3, q["id"]
            for a, b in zip(path, path[1:]):
                assert (a, b) in fks or (b, a) in fks, (q["id"], a, b)
            steps = find_path(graph, path[0], path[-1])  # config B's JOIN path agrees with the gold path
            walked = [path[0]]
            for s in steps:
                walked.append(s["to_table"] if s["from_table"] == walked[-1] else s["from_table"])
            assert walked == path, q["id"]


def test_load_gold_parses_blocks(tmp_path):
    (tmp_path / "g.sql").write_text("-- d01\nSELECT 1;\n\n-- d02\nSELECT\n  2;\n")
    assert load_gold(tmp_path / "g.sql") == {"d01": "SELECT 1", "d02": "SELECT\n  2"}


def test_load_gold_ignores_comments_and_refuses_repeated_ids(tmp_path):
    (tmp_path / "g.sql").write_text("-- d01\n-- note\nSELECT 1;\n")
    assert load_gold(tmp_path / "g.sql") == {"d01": "-- note\nSELECT 1"}
    (tmp_path / "g.sql").write_text("-- d01\nSELECT 1;\n-- d01\nSELECT 2;\n")
    with pytest.raises(ValueError, match="d01"):
        load_gold(tmp_path / "g.sql")


def test_load_questions_skips_blank_lines(tmp_path):
    (tmp_path / "q.jsonl").write_text('{"id": "a"}\n\n{"id": "b"}\n')
    assert load_questions(tmp_path / "q.jsonl") == [{"id": "a"}, {"id": "b"}]


@pytest.mark.parametrize("name, path, p", DATABASES)
def test_value_literals_come_from_the_profile_not_the_question(name, path, p):
    """Value questions need an exact stored value that only config C's context reveals (d11 is the spec's example)."""
    sources = {"model": model(path), "docs": {}, "relationships": [], "profile": profile(path),
               "graph": build_graph(model(path))}
    gold = load_gold("benchmark/gold.sql")
    for q in questions(name):
        if q["kind"] != "value" or q["id"] == "d11":
            continue
        literals = [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", gold[q["id"]])]
        assert literals, q["id"]
        context = build_context("C", q["question"], sources)
        for lit in literals:
            assert lit in context, (q["id"], lit)
            assert lit.lower() not in q["question"].lower(), (q["id"], lit)


def test_load_gold_accepts_prefixed_ids(tmp_path):
    (tmp_path / "g.sql").write_text("-- fd01\nSELECT 1;\n\n-- d02\nSELECT 2;\n")
    assert load_gold(tmp_path / "g.sql") == {"fd01": "SELECT 1", "d02": "SELECT 2"}
