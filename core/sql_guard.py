"""Decide whether a SQL statement may run on an analysed database, and which output columns to mask."""

import sqlite3
import string
import time
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from core.parser import parse_database
from core.profiler import _value, is_sensitive
from core.sqlite import connect_readonly

DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_S = 5.0


# SQLite (and sqlglot's sqlite dialect) fold ASCII letters only; str.casefold would equate "ſ" with "s".
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _fold(name: str) -> str:
    return name.translate(_ASCII_LOWER)


class GuardError(ValueError):
    """kind: "invalid" (refused before running), "execution" (SQLite rejected the query), "timeout",
    or "database" (the file is missing, unreadable or changed since the parse: no rewrite of the query helps)."""

    def __init__(self, kind: Literal["invalid", "execution", "timeout", "database"], message: str):
        super().__init__(message)
        self.kind = kind


def _invalid(message: str) -> GuardError:
    return GuardError("invalid", message)


def _parse(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except (SqlglotError, RecursionError) as err:
        raise _invalid(str(err)) from err
    if not statements:
        raise _invalid("empty query")
    if len(statements) > 1:
        raise _invalid("one statement at a time")
    if not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise _invalid("only SELECT queries are allowed")
    return statements[0]


def _check_tables(root: exp.Expression, model: dict) -> None:
    """Every table must be a model table or a CTE visible in its own scope.

    Runs before qualify, which lets unknown tables through. Table-valued functions have no name.
    A CTE name must not whitelist the same name elsewhere in the tree, so CTE-ness is decided per scope.
    """
    known = {_fold(t["name"]) for t in model["tables"]}
    seen: set[int] = set()
    try:
        scopes = traverse_scope(root)
    except (SqlglotError, RecursionError) as err:
        raise _invalid(str(err)) from err
    for scope in scopes:
        for table in scope.tables:
            seen.add(id(table))
            if not table.db and _fold(table.name) in {_fold(n) for n, v in scope.cte_sources.items() if isinstance(v, Scope)}:
                continue
            if not table.name or _fold(table.name) not in known:
                raise _invalid(f"unknown table {table.sql('sqlite')}")
    for table in root.find_all(exp.Table):  # fail closed on anything no scope covered
        if id(table) not in seen:
            raise _invalid(f"unknown table {table.sql('sqlite')}")


def check_sql(sql: str, model: dict, masked_columns: Iterable[tuple[str, str]] = (), *,
              max_rows: int = DEFAULT_MAX_ROWS) -> dict:
    """Validate `sql` against the model; return the SQL to run, its width and the masked output positions."""
    root = _parse(sql)

    _check_tables(root, model)
    for node in root.find_all(exp.In):
        if node.args.get("field"):  # `x IN tablename` reads a table that sqlglot parses as a column
            raise _invalid(f"IN {node.args['field'].sql('sqlite')} is not allowed; use IN (SELECT ...)")

    # `LIMIT -1` is an exp.Neg and `LIMIT (SELECT ..)` a subquery: neither counts as a literal.
    limit = root.args.get("limit")
    bound = limit.expression if limit else None
    keep = isinstance(bound, exp.Literal) and bound.is_int and 0 <= int(bound.this) <= max_rows
    if not keep:
        root = root.limit(max_rows)

    # The types are irrelevant here, and sqlglot rejects some free-form SQLite type names.
    schema = {t["name"]: {c["name"]: "TEXT" for c in t["columns"]} for t in model["tables"]}
    try:
        qualified = qualify(root.copy(), schema=schema, dialect="sqlite")
        masked = _masked_positions(qualified, model, masked_columns)
    except (SqlglotError, RecursionError) as err:
        raise _invalid(str(err)) from err

    return {"sql": root.sql(dialect="sqlite"), "width": len(qualified.selects),
            "masked": masked, "capped": not keep}


def _masked_positions(qualified: exp.Expression, model: dict, marks: Iterable[tuple[str, str]]) -> list[int]:
    marked = {(_fold(t), _fold(c)) for t, c in marks}
    masked = {(_fold(t["name"]), _fold(c["name"])): f"{t['name']}.{c['name']}"
              for t in model["tables"] for c in t["columns"]
              if is_sensitive(c["name"]) or (_fold(t["name"]), _fold(c["name"])) in marked}
    projections = qualified.selects if isinstance(qualified, exp.Select) else None
    positions: dict[int, str] = {}  # output position -> Table.Column

    def refuse(name: str):
        return _invalid(f"column {name} is masked: select it as a plain column to get ***; it cannot be used "
                        "in expressions, filters, joins, sorting, grouping, subqueries or set operations")

    for scope in traverse_scope(qualified):
        for column in scope.columns:
            source, s = None, scope
            while source is None and s is not None:  # walk up for correlated references
                source, s = s.sources.get(column.table), s.parent
            if not isinstance(source, exp.Table):  # CTEs and derived tables are checked in their own scopes
                continue
            key = (_fold(source.name), _fold(column.name))
            if key not in masked:
                continue
            top = column.parent if isinstance(column.parent, exp.Alias) else column
            if (scope.is_root and projections is not None and top.parent is qualified and top.arg_key == "expressions"
                    and not qualified.args.get("distinct")):  # DISTINCT row counts leak duplicates
                i = next(i for i, p in enumerate(projections) if p is top)  # identity: `SELECT Email, Email`
                positions[i] = masked[key]
            else:
                raise refuse(masked[key])

    # qualify rewrites ORDER BY 1 / ORDER BY alias into a bare reference that traverse_scope does not list.
    order = qualified.args.get("order") if projections is not None else None
    if order and positions:
        by_name = {_fold(projections[i].alias_or_name): name for i, name in positions.items()}
        for column in order.find_all(exp.Column):
            if not column.table and _fold(column.name) in by_name:
                raise refuse(by_name[_fold(column.name)])
    # SQLite strips parentheses, COLLATE and unary signs before reading a term as a column number, and qualify only
    # rewrites a bare one: refuse any column-free term with an integer literal (ORDER BY random() still passes).
    if positions:
        for arg in ("order", "group"):
            for term in (qualified.args.get(arg) or exp.Tuple()).expressions:
                if not term.find(exp.Column) and any(lit.is_int for lit in term.find_all(exp.Literal)):
                    raise refuse(next(iter(positions.values())))
    return sorted(positions)


def run_sql(db_path: str | Path, sql: str, model: dict, masked_columns: Iterable[tuple[str, str]] = (), *,
            max_rows: int = DEFAULT_MAX_ROWS, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Check, then run read-only with a deadline; masked columns come back as MASK. Rows are lists so duplicate names survive."""
    masked_columns = list(masked_columns)  # may be a generator; read twice
    check = check_sql(sql, model, masked_columns, max_rows=max_rows)
    deadline = time.monotonic() + timeout_s
    fired = False

    def over_deadline() -> bool:
        nonlocal fired
        fired = fired or time.monotonic() > deadline
        return fired

    marked = {_fold(column) for _, column in masked_columns}
    try:
        conn = connect_readonly(db_path)
    except sqlite3.Error as err:
        raise GuardError("database", f"cannot open the database file: {err}") from err
    with closing(conn):
        # Never raise on bad UTF-8: CPython's error message would quote the (possibly masked) cell text.
        conn.text_factory = lambda b: b.decode("utf-8", "replace")
        conn.set_progress_handler(over_deadline, 1000)
        try:
            cursor = conn.execute(check["sql"])
            columns = [d[0] for d in cursor.description]
            if len(columns) != check["width"]:
                raise _failure(db_path, model, "the query's output columns differ from the parsed schema; "
                                               "list the columns instead of *")
            # Fail closed on a same-width stale model: also mask by output name (patterns and user marks).
            masked = set(check["masked"]) | {i for i, name in enumerate(columns)
                                             if is_sensitive(name) or _fold(name) in marked}
            rows = [[_value(v, i in masked) for i, v in enumerate(row)] for row in cursor.fetchall()]
        except sqlite3.Error as err:
            if fired:
                raise GuardError("timeout", f"query took longer than {timeout_s:g} s") from err
            raise _failure(db_path, model, str(err)) from err
    return {"sql": check["sql"], "columns": columns, "rows": rows, "masked": [columns[i] for i in sorted(masked)],
            "limited": check["capped"] and len(rows) == max_rows}


def _failure(db_path: str | Path, model: dict, message: str) -> GuardError:
    """Blame the query only if the file still matches the model; runs on the failure path, so the re-parse is free."""
    try:
        current = {t["name"]: [c["name"] for c in t["columns"]] for t in parse_database(db_path)["tables"]}
    except (sqlite3.Error, SqlglotError) as err:
        return GuardError("database", f"cannot read the database file: {err}")
    if any(current.get(t["name"]) != [c["name"] for c in t["columns"]] for t in model["tables"]):
        return GuardError("database", "the database changed since the last parse; run parse again")
    return GuardError("execution", message)
