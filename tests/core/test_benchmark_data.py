import re

import pytest

from core.evaluation import build_context, load_gold, load_questions
from core.graph import build_graph, find_path
from core.parser import parse_database
from core.profiler import profile_database
from core.sql_guard import run_sql

MODEL = parse_database("data/chinook.db")
DB = "data/chinook.db"
GOLD_IDS = [f"d{i:02}" for i in range(1, 16)]


def test_counts_and_ids():
    qs = load_questions("benchmark/questions.jsonl")
    assert [q["id"] for q in qs] == [f"s{i:02}" for i in range(1, 21)] + [f"d{i:02}" for i in range(1, 21)]
    kinds = [q["kind"] for q in qs]
    assert kinds == [k for k in ("columns", "relationship", "path", "unanswerable") for _ in range(5)] + \
                    [k for k in ("simple", "join", "value", "unanswerable") for _ in range(5)]
    assert [q["category"] for q in qs] == ["schema"] * 20 + ["data"] * 20
    assert {q["database"] for q in qs} == {"chinook"}
    assert all(q["question"].strip() for q in qs)


def test_gold_covers_exactly_the_answerable_data_questions():
    qs = load_questions("benchmark/questions.jsonl")
    gold = load_gold("benchmark/gold.sql")
    assert set(gold) == {q["id"] for q in qs if q["category"] == "data" and q["kind"] != "unanswerable"}
    assert set(gold) == set(GOLD_IDS)


@pytest.mark.parametrize("qid", GOLD_IDS)
def test_gold_runs_through_the_guard(qid):
    out = run_sql(DB, load_gold("benchmark/gold.sql")[qid], MODEL)
    assert 1 <= len(out["rows"]) <= 99 and not out["masked"]


def test_schema_expectations_exist_in_the_model():
    tables = {t["name"]: {c["name"] for c in t["columns"]} for t in MODEL["tables"]}
    fks = {(t["name"], fk["ref_table"]) for t in MODEL["tables"] for fk in t["foreign_keys"]}
    fk_columns = {(f"{t['name']}.{c}", f"{fk['ref_table']}.{r}") for t in MODEL["tables"]
                  for fk in t["foreign_keys"] for c, r in zip(fk["columns"], fk["ref_columns"])}
    graph = build_graph(MODEL)
    for q in load_questions("benchmark/questions.jsonl"):
        expected = q["expected"]
        if q["category"] == "data" or q["kind"] == "unanswerable":
            assert expected == {}, q["id"]
            continue
        keys = {"columns": {"tables", "columns"}, "relationship": {"tables", "columns", "path"},
                "path": {"tables", "path"}}[q["kind"]]
        assert set(expected) == keys, q["id"]
        assert expected["tables"] and set(expected["tables"]) <= set(tables), q["id"]
        for name in expected.get("columns", []):
            table, column = name.split(".")
            assert column in tables[table], name
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


def test_value_literals_come_from_the_profile_not_the_question():
    """Value questions need an exact stored value that only config C's context reveals (d11 is the spec's example)."""
    sources = {"model": MODEL, "docs": {}, "relationships": [], "profile": profile_database(DB, MODEL),
               "graph": build_graph(MODEL)}
    gold = load_gold("benchmark/gold.sql")
    for q in load_questions("benchmark/questions.jsonl"):
        if q["kind"] != "value" or q["id"] == "d11":
            continue
        literals = [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", gold[q["id"]])]
        assert literals, q["id"]
        context = build_context("C", q["question"], sources)
        for lit in literals:
            assert lit in context, (q["id"], lit)
            assert lit.lower() not in q["question"].lower(), (q["id"], lit)
