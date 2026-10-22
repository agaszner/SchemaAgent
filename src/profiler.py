"""Data profile per column, masked sample rows, and value-based FK candidates."""

import json
import sqlite3
import sys
from collections.abc import Collection, Iterable
from contextlib import closing
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


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def name_score(column: str, ref_table: str, ref_column: str) -> float:
    """How well a column name points at ref_table.ref_column: 1.0 exact, less for looser forms, 0.0 none.

    CustomerId -> Customer.CustomerId, customer_id -> customer.id and playerID -> People.playerID
    score 1.0; manager_staff_id -> staff.staff_id 0.8; cds -> schools.CDSCode 0.7; ID -> Patient.ID 0.5.
    """
    s, k, t = _norm(column), _norm(ref_column), _norm(ref_table)
    prefixed = {t + k, t.removesuffix("s") + k}  # Customers + ID -> customerid
    if s in prefixed or (s == k and k != "id"):
        return 1.0
    if any(s.endswith(p) for p in prefixed) or (len(k) >= 4 and s.endswith(k)):
        return 0.8
    if len(s) >= 3 and k.startswith(s):
        return 0.7
    if s == k:  # ID -> Patient.ID: every table may have an "id", so the name alone says little
        return 0.5
    return 0.0


def _unique_keys(conn: sqlite3.Connection, table: dict) -> list[list[str]]:
    """Column lists that are unique in the table: the primary key and full unique indexes."""
    keys = [table["primary_key"]] if table["primary_key"] else []
    for _, index, unique, _, partial in conn.execute(f"PRAGMA index_list({quote(table['name'])})"):
        if unique and not partial:
            columns = [c for *_, c in conn.execute(f"PRAGMA index_info({quote(index)})")]
            if None not in columns and set(columns) not in map(set, keys):  # None: an expression index
                keys.append(columns)
    return keys


def _is_unique(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True when the data makes the column a key: no NULLs and no repeats in a non-empty table."""
    c = quote(column)
    total, filled, distinct = conn.execute(
        f"SELECT COUNT(*), COUNT({c}), COUNT(DISTINCT {c}) FROM {quote(table)}").fetchone()
    return total > 0 and total == filled == distinct


def find_fk_candidates(conn: sqlite3.Connection, model: dict, min_coverage: float = 0.9) -> list[dict]:
    """Columns that name another table's key and whose values mostly appear among it.

    Both kinds of evidence are required, not either one as the spec allows: surrogate ids
    (1..N) make nearly every integer column a value subset of every key, so values alone
    proposed ~100 wrong pairs on formula_1. Targets are primary keys, unique indexes and
    columns the data shows unique in tables without a primary key, so keyless databases
    (Lahman) still get candidates; in keyed tables data-unique columns are coincidences
    (sakila's two staff rows make staff.store_id unique).
    A composite key matches only when the source has every key column by its exact name.
    confidence = coverage x name_score.
    """
    tables = {t["name"]: t for t in model["tables"]}
    keys = {name: _unique_keys(conn, t) for name, t in tables.items()}
    unique_cache: dict[tuple[str, str], bool] = {}

    def is_key(table: str, column: str) -> bool:
        if [column] in keys[table]:
            return True
        if tables[table]["primary_key"]:
            return False
        # ponytail: tiny keyless tables look unique by chance (2 rows, stint 1 and 2); add a min row count if it bites
        if (table, column) not in unique_cache:
            unique_cache[(table, column)] = _is_unique(conn, table, column)
        return unique_cache[(table, column)]

    def matches(table: dict) -> list[tuple[list[str], str, list[str], float]]:
        """(columns, ref_table, ref_columns, name score) pairs the names suggest for this source table."""
        own = {_norm(c["name"]): c["name"] for c in table["columns"]}
        found = []
        for ref in tables.values():
            for key in keys[ref["name"]]:
                if len(key) > 1 and all(_norm(k) in own for k in key):
                    found.append(([own[_norm(k)] for k in key], ref["name"], key, 1.0))
            for column in table["columns"]:
                for ref_column in ref["columns"]:
                    score = name_score(column["name"], ref["name"], ref_column["name"])
                    if score and is_key(ref["name"], ref_column["name"]):
                        found.append(([column["name"]], ref["name"], [ref_column["name"]], score))
        return found

    candidates = []
    for table in tables.values():
        declared = [fk["columns"] for fk in table["foreign_keys"]]
        for columns, ref_table, ref_columns, score in matches(table):
            if (ref_table, ref_columns) == (table["name"], columns) or columns in declared:
                continue
            if set(columns) in map(set, keys[table["name"]]) and (len(columns) > 1 or score <= 0.5):
                continue  # key to key: twin fact tables (Batting/Pitching) or two bare IDs (Parks.ID, People.ID)
            share = value_coverage(conn, table["name"], columns, ref_table, ref_columns)
            if share < min_coverage:
                continue
            candidates.append({
                "table": table["name"], "columns": columns,
                "ref_table": ref_table, "ref_columns": ref_columns,
                "coverage": share,
                "confidence": round(share * score, 2),
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
