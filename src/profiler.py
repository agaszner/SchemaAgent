"""Data profile per column, masked sample rows, and value-based FK candidates."""

import json
import sqlite3
import sys
from collections.abc import Collection, Iterable
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
                  top_k: int = 5, sample_size: int = 3, masked_columns: Collection[str] = ()) -> dict:
    """Column stats and sample rows; masked_columns names this table's user-marked columns."""
    def is_masked(name: str) -> bool:
        return is_sensitive(name, patterns) or name in masked_columns

    t = quote(table["name"])
    row_count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    columns = []
    for column in table["columns"]:
        c = quote(column["name"])
        masked = is_masked(column["name"])
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
    samples = [{n: _value(v, is_masked(n)) for n, v in zip(names, row)} for row in cursor]
    return {"table": table["name"], "row_count": row_count, "columns": columns, "sample_rows": samples}


def distinct_values(conn: sqlite3.Connection, table: str, columns: list[str]) -> set[tuple]:
    """Distinct value tuples of the columns, skipping rows where any of them is NULL."""
    quoted = [quote(c) for c in columns]
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in quoted)
    rows = conn.execute(f"SELECT DISTINCT {', '.join(quoted)} FROM {quote(table)} WHERE {not_null}")
    return {tuple(row) for row in rows}


def coverage(values: set, keys: set) -> float:
    """Share of values found among keys, 0.0 when there are no values."""
    return len(values & keys) / len(values) if values else 0.0


def value_coverage(conn: sqlite3.Connection, table: str, columns: list[str],
                   ref_table: str, ref_columns: list[str]) -> float:
    """Rounded share of the columns' distinct value tuples found in ref_columns; works for composite keys."""
    values = distinct_values(conn, table, columns)
    keys = distinct_values(conn, ref_table, ref_columns)
    return round(coverage(values, keys), 4)


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
            targets[(table["name"], key)] = distinct_values(conn, table["name"], [key])

    candidates = []
    for table in model["tables"]:
        declared = {c for fk in table["foreign_keys"] for c in fk["columns"]}
        own_key = table["primary_key"] if len(table["primary_key"]) == 1 else []
        for column in table["columns"]:
            name = column["name"]
            if name in declared or name in own_key:
                continue
            values = distinct_values(conn, table["name"], [name])
            if len(values) < 2:  # constants like Quantity=1 fit every key range
                continue
            for (ref_table, ref_column), keys in targets.items():
                share = coverage(values, keys)
                if share < min_coverage:
                    continue
                similarity = SequenceMatcher(None, name.lower(), ref_column.lower()).ratio()
                candidates.append({
                    "table": table["name"], "columns": [name],
                    "ref_table": ref_table, "ref_columns": [ref_column],
                    "coverage": round(share, 4),
                    "confidence": round((share + similarity) / 2, 2),
                })
    return sorted(candidates, key=lambda c: -c["confidence"])


def profile_database(db_path: str | Path, model: dict | None = None, patterns=SENSITIVE_PATTERNS,
                     masked_columns: Iterable[tuple[str, str]] = ()) -> dict:
    model = model or parse_database(db_path)
    marks = list(masked_columns)  # an iterator would run dry after the first table
    with closing(connect_readonly(db_path)) as conn:
        return {
            "tables": [profile_table(conn, t, patterns, masked_columns={c for n, c in marks if n == t["name"]})
                       for t in model["tables"]],
            "fk_candidates": find_fk_candidates(conn, model),
        }


if __name__ == "__main__":
    print(json.dumps(profile_database(sys.argv[1]), indent=2, default=str))
