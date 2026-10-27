import sqlite3

import pytest

from core.parser import parse_database
from core.profiler import MASK
from core.rows import read_rows

DB = "data/chinook.db"
_TABLES = {t["name"]: t for t in parse_database(DB)["tables"]}
TRACK, CUSTOMER = _TABLES["Track"], _TABLES["Customer"]


def test_pages_through_track():
    page = read_rows(DB, TRACK)
    assert page["total"] == 3503 and len(page["rows"]) == 25 and page["rows"][0]["TrackId"] == 1
    assert page["columns"][:2] == ["TrackId", "Name"]
    assert len(read_rows(DB, TRACK, offset=3500)["rows"]) == 3


def test_sorts_descending():
    assert read_rows(DB, TRACK, sort="Milliseconds", descending=True, limit=1)["rows"][0]["TrackId"] == 2820


def test_filters_contains_and_equals():
    assert read_rows(DB, TRACK, column="Composer", value="AC/DC")["total"] == 8
    assert read_rows(DB, TRACK, column="AlbumId", op="equals", value="1")["total"] == 10


def test_masks_pattern_and_marked_columns():
    page = read_rows(DB, CUSTOMER, masked_columns={"FirstName"})
    assert page["rows"][0]["Email"] == MASK and page["rows"][0]["FirstName"] == MASK
    assert {"Email", "Phone", "Fax", "Address", "FirstName"} == set(page["masked"])
    assert page["rows"][0]["LastName"] == "Gonçalves"


@pytest.mark.parametrize("kwargs", [
    {"column": "Email", "value": "a"}, {"sort": "Email"}, {"sort": "Nope"}, {"column": "Nope", "value": "x"},
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"column": "Country", "op": "like", "value": "x"}])
def test_rejects_bad_requests(kwargs):
    with pytest.raises(ValueError):
        read_rows(DB, CUSTOMER, **kwargs)


def test_quotes_names_and_escapes_wildcards(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE "Order Details" ("Unit Price" REAL, "Note" TEXT)')
    conn.executemany('INSERT INTO "Order Details" VALUES (?, ?)', [(1.5, "50%"), (2.0, "a_b"), (3.0, "abc")])
    conn.commit()
    conn.close()
    table = parse_database(path)["tables"][0]
    assert [r["Note"] for r in read_rows(path, table, column="Note", value="%")["rows"]] == ["50%"]
    assert [r["Note"] for r in read_rows(path, table, column="Note", value="_")["rows"]] == ["a_b"]
    assert read_rows(path, table, sort="Unit Price", descending=True)["rows"][0]["Unit Price"] == 3.0


def test_masks_column_added_after_parse(tmp_path):
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    table = parse_database(path)["tables"][0]
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE t ADD COLUMN email TEXT")
        conn.execute("UPDATE t SET email = 'a@b.c'")
    page = read_rows(path, table)
    assert page["rows"][0]["email"] == MASK and "email" in page["masked"]
