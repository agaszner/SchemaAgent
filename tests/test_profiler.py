import json
import sqlite3
from contextlib import closing

from src.parser import parse_database
from src.profiler import MASK, profile_database, value_coverage
from src.schema_graph import build_graph, find_path
from src.sql_tools import connect_readonly

DB = "data/chinook.db"


def test_profile_never_leaks_sensitive_values():
    profile = profile_database(DB)
    assert "@" not in json.dumps(profile, default=str)
    customer = next(t for t in profile["tables"] if t["table"] == "Customer")
    email = next(c for c in customer["columns"] if c["name"] == "Email")
    assert email["masked"] and email["min"] == email["max"] == MASK
    assert all(row["Phone"] in (MASK, None) for row in customer["sample_rows"])


def test_finds_undeclared_fk_from_values(tmp_path):
    db = tmp_path / "shop.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("""
            CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Orders (OrderId INTEGER PRIMARY KEY, CustomerId INTEGER, Qty INTEGER);
            INSERT INTO Customer VALUES (1, 'a'), (2, 'b'), (3, 'c');
            INSERT INTO Orders VALUES (1, 1, 1), (2, 3, 1), (3, 3, 1), (4, 2, 1);
        """)
    candidates = profile_database(db)["fk_candidates"]
    best = candidates[0]
    assert (best["table"], best["columns"], best["ref_table"]) == ("Orders", ["CustomerId"], "Customer")
    assert not any(c["columns"] == ["Qty"] for c in candidates)


def test_find_path_customer_to_track():
    graph = build_graph(parse_database(DB))
    steps = find_path(graph, "Customer", "Track")
    assert [(s["from_table"], s["to_table"]) for s in steps] == [
        ("Invoice", "Customer"), ("InvoiceLine", "Invoice"), ("InvoiceLine", "Track"),
    ]
    assert steps[0]["from_columns"] == ["CustomerId"]


def test_find_path_skips_rejected_and_penalises_proposed():
    model = parse_database(DB)
    shortcut = {"table": "Customer", "columns": ["CustomerId"], "ref_table": "Track",
                "ref_columns": ["TrackId"], "confidence": 0.6}
    graph = build_graph(model, [shortcut])
    assert len(find_path(graph, "Customer", "Track")) == 3  # 3 certain hops beat one weak guess

    for _, _, edge in graph.edges(data=True):
        if edge["from_table"] == "Invoice" and edge["to_table"] == "Customer":
            edge["status"] = "rejected"
    steps = find_path(graph, "Customer", "Track")
    assert [s["source"] for s in steps] == ["inferred"]


def test_value_coverage_compares_composite_keys(tmp_path):
    db = tmp_path / "pairs.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("""
            CREATE TABLE a (x, y);
            CREATE TABLE b (x, y);
            INSERT INTO a VALUES (1, 1), (1, 2), (2, 2), (2, 2), (NULL, 1);
            INSERT INTO b VALUES (1, 1), (2, 2);
        """)
    with closing(connect_readonly(db)) as conn:
        assert value_coverage(conn, "a", ["x", "y"], "b", ["x", "y"]) == 0.6667
    with closing(connect_readonly(DB)) as conn:
        assert value_coverage(conn, "Invoice", ["CustomerId"], "Customer", ["CustomerId"]) == 1.0
        assert value_coverage(conn, "Track", ["GenreId"], "MediaType", ["MediaTypeId"]) == 0.2


def test_marked_column_is_masked_without_masking_namesakes():
    tables = {t["table"]: t for t in profile_database(DB, masked_columns=[("Genre", "Name")])["tables"]}
    name = next(c for c in tables["Genre"]["columns"] if c["name"] == "Name")
    assert name["masked"] and name["min"] == name["max"] == MASK and name["top_values"] == []
    assert all(row["Name"] == MASK for row in tables["Genre"]["sample_rows"])
    assert not next(c for c in tables["Track"]["columns"] if c["name"] == "Name")["masked"]
