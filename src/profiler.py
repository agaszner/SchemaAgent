"""Data profile per column, masked sample rows, and value-based FK candidates."""

import json
import sqlite3
import sys
from contextlib import closing
from difflib import SequenceMatcher
from pathlib import Path

from src.parser import parse_database
from src.sql_tools import connect_readonly, quote

SENSITIVE_PATTERNS = ("email", "phone", "fax", "address")
MASK = "***"


def is_sensitive(column: str, patterns=SENSITIVE_PATTERNS) -> bool:
    return any(p in column.lower() for p in patterns)


def _value(value, masked: bool):
    """Make a value safe for the LLM: masked if sensitive, JSON-friendly if binary."""
    if value is None:
        return None
    if masked:
        return MASK
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


def profile_table(conn: sqlite3.Connection, table: dict, patterns=SENSITIVE_PATTERNS,
                  top_k: int = 5, sample_size: int = 3) -> dict:
    t = quote(table["name"])
    row_count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    columns = []
    for column in table["columns"]:
        c = quote(column["name"])
        masked = is_sensitive(column["name"], patterns)
        nulls, distinct, low, high = conn.execute(
            f"SELECT SUM({c} IS NULL), COUNT(DISTINCT {c}), MIN({c}), MAX({c}) FROM {t}"
        ).fetchone()
        top = conn.execute(
            f"SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c} ORDER BY COUNT(*) DESC LIMIT ?",
            (top_k,),
        ).fetchall()
        columns.append({
            "name": column["name"],
            "masked": masked,
            "null_ratio": round((nulls or 0) / row_count, 4) if row_count else 0.0,
            "distinct": distinct,
            "min": _value(low, masked),
            "max": _value(high, masked),
            "top_values": [] if masked else [{"value": _value(v, False), "count": n} for v, n in top],
        })

    cursor = conn.execute(f"SELECT * FROM {t} LIMIT ?", (sample_size,))
    names = [d[0] for d in cursor.description]
    samples = [{n: _value(v, is_sensitive(n, patterns)) for n, v in zip(names, row)} for row in cursor]
    return {"table": table["name"], "row_count": row_count, "columns": columns, "sample_rows": samples}


def find_fk_candidates(conn: sqlite3.Connection, model: dict, min_coverage: float = 1.0) -> list[dict]:
    """Columns whose values all appear among another table's single-column key.

    Surrogate keys (1..N) make every small id range a subset of every bigger one, so
    sources that are their own table's key, already have a declared FK, or hold a
    single value are skipped.
    """
    targets = {}
    for table in model["tables"]:
        if len(table["primary_key"]) == 1:
            key = table["primary_key"][0]
            values = conn.execute(f"SELECT DISTINCT {quote(key)} FROM {quote(table['name'])}").fetchall()
            targets[(table["name"], key)] = {v for (v,) in values}

    candidates = []
    for table in model["tables"]:
        declared = {c for fk in table["foreign_keys"] for c in fk["columns"]}
        own_key = table["primary_key"] if len(table["primary_key"]) == 1 else []
        for column in table["columns"]:
            name = column["name"]
            if name in declared or name in own_key:
                continue
            values = conn.execute(
                f"SELECT DISTINCT {quote(name)} FROM {quote(table['name'])} WHERE {quote(name)} IS NOT NULL"
            ).fetchall()
            values = {v for (v,) in values}
            if len(values) < 2:  # constants like Quantity=1 fit every key range
                continue
            for (ref_table, ref_column), keys in targets.items():
                coverage = len(values & keys) / len(values)
                if coverage < min_coverage:
                    continue
                similarity = SequenceMatcher(None, name.lower(), ref_column.lower()).ratio()
                candidates.append({
                    "table": table["name"], "columns": [name],
                    "ref_table": ref_table, "ref_columns": [ref_column],
                    "coverage": round(coverage, 4),
                    "confidence": round((coverage + similarity) / 2, 2),
                })
    return sorted(candidates, key=lambda c: -c["confidence"])


def profile_database(db_path: str | Path, model: dict | None = None, patterns=SENSITIVE_PATTERNS) -> dict:
    model = model or parse_database(db_path)
    with closing(connect_readonly(db_path)) as conn:
        return {
            "tables": [profile_table(conn, t, patterns) for t in model["tables"]],
            "fk_candidates": find_fk_candidates(conn, model),
        }


if __name__ == "__main__":
    print(json.dumps(profile_database(sys.argv[1]), indent=2, default=str))
