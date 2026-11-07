import copy

import pytest

from core.evaluation import CONFIGS, build_context, mentioned_tables, raw_ddl
from core.graph import build_graph
from core.parser import parse_database
from core.profiler import profile_database

DB = "data/chinook.db"
MODEL = parse_database(DB)


def _sources():
    rels = [
        {"from_table": t["name"], "from_columns": fk["columns"], "to_table": fk["ref_table"],
         "to_columns": fk["ref_columns"], "source": "declared", "status": "accepted", "confidence": 1.0}
        for t in MODEL["tables"] for fk in t["foreign_keys"]
    ]
    docs = {t["name"]: {"summary": f"About {t['name']}.",
                        "columns": [{"name": c["name"], "description": "d", "certainty": "certain"} for c in t["columns"]]}
            for t in MODEL["tables"]}
    return {"ddl": raw_ddl(DB), "model": MODEL, "docs": docs, "relationships": rels,
            "profile": profile_database(DB, MODEL), "graph": build_graph(MODEL)}


SOURCES = _sources()


def test_configs():
    assert CONFIGS == ("A", "B", "C")


def test_a_is_raw_ddl_only():
    ctx = build_context("A", "q", SOURCES)
    assert ctx.startswith("## Schema (DDL)") and "CREATE TABLE [Album]" in ctx
    assert "## Tables" not in ctx and "About Album." not in ctx


def test_b_has_docs_relationships_and_join_path():
    ctx = build_context("B", "How do I get from customers to the tracks they bought?", SOURCES)
    assert "## Tables" in ctx and "## Relationships" in ctx and "## JOIN path" in ctx
    assert "Invoice" in ctx.split("## JOIN path")[1] and "## Data profile" not in ctx
    assert "About Album." in ctx and "Customer.CustomerId = Invoice.CustomerId (accepted)" in ctx


@pytest.mark.parametrize("config", ["B", "C"])
def test_join_path_comes_last_so_the_prefix_is_shared(config):
    plain = build_context(config, "How many genres?", SOURCES)
    with_path = build_context(config, "How do I get from customers to the tracks they bought?", SOURCES)
    assert with_path.startswith(plain + "\n\n## JOIN path\n") and "## Data profile" not in with_path.split("## JOIN path")[1]


def test_join_path_omitted_for_one_table():
    assert "## JOIN path" not in build_context("B", "How many genres?", SOURCES)


def test_proposed_and_rejected():
    s = copy.copy(SOURCES)
    base = {"source": "inferred", "confidence": 0.8}
    s["relationships"] = SOURCES["relationships"] + [
        {"from_table": "Track", "from_columns": ["Name"], "to_table": "Genre", "to_columns": ["Name"], "status": "proposed", **base},
        {"from_table": "Album", "from_columns": ["Title"], "to_table": "Genre", "to_columns": ["Name"], "status": "rejected", **base},
    ]
    rels = build_context("B", "q", s).split("## Relationships")[1]
    assert "Track.Name -> Genre.Name" in rels and "(assumed)" in rels
    assert "Album.Title" not in rels


def test_c_adds_masked_profile_and_never_raw_personal_data():
    ctx = build_context("C", "q", SOURCES)
    assert "## Data profile" in ctx and "Rock" in ctx
    assert "luisg@embraer.com.br" not in ctx and "***" in ctx


def test_mentioned_tables():
    assert mentioned_tables("How do I get from customers to the tracks they bought?", MODEL) == ["Customer", "Track"]
    assert mentioned_tables("Which invoice lines belong to an invoice?", MODEL) == ["InvoiceLine", "Invoice"]
    assert mentioned_tables("How many genres?", MODEL) == ["Genre"]


def _tables(ctx):
    return ctx.split("## Relationships")[0]


def test_fk_marker_comes_from_non_rejected_relationships():
    assert "FK → Customer.CustomerId" in _tables(build_context("B", "q", SOURCES))  # control
    s = copy.copy(SOURCES)
    s["relationships"] = [{**r, "status": "rejected"} if (r["from_table"], r["to_table"]) == ("Invoice", "Customer")
                          else r for r in SOURCES["relationships"]]
    assert "FK → Customer.CustomerId" not in _tables(build_context("B", "q", s))


def test_proposed_fk_marker_is_assumed():
    s = copy.copy(SOURCES)
    s["relationships"] = SOURCES["relationships"] + [
        {"from_table": "Track", "from_columns": ["Name"], "to_table": "Genre", "to_columns": ["Name"],
         "source": "inferred", "status": "proposed", "confidence": 0.8}]
    tables = _tables(build_context("B", "q", s))
    assert "- Name VARCHAR(200) FK → Genre.Name (assumed)" in tables
    assert "FK → Genre.GenreId" in tables and "FK → Genre.GenreId (assumed)" not in tables


def test_join_path_in_fk_direction():
    path = build_context("B", "List invoices with their customers", SOURCES).split("## JOIN path")[1]
    assert "Invoice.CustomerId = Customer.CustomerId (accepted)" in path


def test_proposed_step_in_join_path_is_assumed():
    s = copy.copy(SOURCES)
    s["graph"] = build_graph(MODEL, [{"table": "Customer", "columns": ["CustomerId"], "ref_table": "Track",
                                      "ref_columns": ["TrackId"], "confidence": 1.0}])
    path = build_context("B", "How do I get from customers to the tracks they bought?", s).split("## JOIN path")[1]
    assert "Customer.CustomerId = Track.TrackId (assumed)" in path
