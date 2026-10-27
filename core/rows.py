"""One page of a table's rows, read-only, with sensitive and user-marked columns masked."""

from collections.abc import Collection
from contextlib import closing
from pathlib import Path

from core.profiler import _value, is_sensitive
from core.sqlite import connect_readonly, quote

MAX_LIMIT = 100
OPS = ("contains", "equals")


def read_rows(db_path: str | Path, table: dict, masked_columns: Collection[str] = (), *,
              offset: int = 0, limit: int = 25, sort: str | None = None, descending: bool = False,
              column: str | None = None, op: str = "contains", value: str | None = None) -> dict:
    """Page of rows plus the number of rows matching the filter; ValueError on a bad request."""
    names = [c["name"] for c in table["columns"]]
    filtering = column is not None and value is not None
    if not 1 <= limit <= MAX_LIMIT or offset < 0:
        raise ValueError(f"limit must be 1..{MAX_LIMIT} and offset >= 0")
    if op not in OPS:
        raise ValueError(f"op must be one of {OPS}")
    for name in (sort, column if filtering else None):
        if name is None:
            continue
        if name not in names:
            raise ValueError(f"unknown column {name!r}")
        if is_sensitive(name) or name in masked_columns:
            raise ValueError(f"column {name!r} is masked")

    where, params = "", []
    if filtering:
        c = quote(column)
        if op == "equals":
            where, params = f" WHERE {c} = ?", [value]
        else:
            escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where, params = f" WHERE CAST({c} AS TEXT) LIKE ? ESCAPE '\\'", [f"%{escaped}%"]
    order = f" ORDER BY {quote(sort)}{' DESC' if descending else ''}" if sort else ""

    t = quote(table["name"])
    with closing(connect_readonly(db_path)) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {t}{where}", params).fetchone()[0]
        cursor = conn.execute(f"SELECT * FROM {t}{where}{order} LIMIT ? OFFSET ?", [*params, limit, offset])
        columns = [d[0] for d in cursor.description]
        masked = [n for n in columns if is_sensitive(n) or n in masked_columns]  # the file may have gained columns
        rows = [{n: _value(v, n in masked) for n, v in zip(columns, row)} for row in cursor]
    return {"columns": columns, "masked": masked, "rows": rows, "total": total}
