import sqlite3
from contextlib import closing

import pytest

from core.parser import parse_database
from core.sqlite import affinity, connect_readonly, quote

DB = "data/chinook.db"


def test_parser_matches_sqlite_pragmas():
    model = parse_database(DB)
    assert len(model["tables"]) == 11
    with closing(sqlite3.connect(DB)) as conn:
        for table in model["tables"]:
            info = conn.execute(f"PRAGMA table_info({quote(table['name'])})").fetchall()
            assert [c["name"] for c in table["columns"]] == [row[1] for row in info]
            pk = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
            assert table["primary_key"] == pk

            pragma_fks = {(row[2], row[3], row[4])
                          for row in conn.execute(f"PRAGMA foreign_key_list({quote(table['name'])})")}
            parsed_fks = {(fk["ref_table"], col, ref)
                          for fk in table["foreign_keys"]
                          for col, ref in zip(fk["columns"], fk["ref_columns"])}
            assert parsed_fks == pragma_fks


def test_readonly_connection_rejects_writes():
    with closing(connect_readonly(DB)) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM Genre")


def test_affinity_follows_sqlite_rules():
    types = ["INT", "VARCHAR(60)", "CLOB", None, "DOUBLE", "DECIMAL(10, 2)", "DATETIME"]
    assert [affinity(t) for t in types] == ["INTEGER", "TEXT", "TEXT", "BLOB", "REAL", "NUMERIC", "NUMERIC"]
