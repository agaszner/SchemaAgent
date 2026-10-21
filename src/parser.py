"""DDL -> JSON data model: tables, columns, types, primary and foreign keys."""

import json
import sys
from contextlib import closing
from pathlib import Path

import sqlglot
from sqlglot import exp

from src.sql_tools import connect_readonly


def _foreign_key(columns: list[str], reference: exp.Reference) -> dict:
    target = reference.this  # Schema(Table, [columns]) or bare Table
    if isinstance(target, exp.Schema):
        return {"columns": columns, "ref_table": target.this.name,
                "ref_columns": [c.name for c in target.expressions]}
    return {"columns": columns, "ref_table": target.name, "ref_columns": []}


def parse_ddl(ddl: str) -> dict:
    schema = sqlglot.parse_one(ddl, read="sqlite").this
    table = {"name": schema.this.name, "columns": [], "primary_key": [], "foreign_keys": []}

    constraints = []
    for node in schema.expressions:
        if isinstance(node, exp.ColumnDef):
            kinds = [c.kind for c in node.constraints]
            table["columns"].append({
                "name": node.name,
                "type": node.kind.sql() if node.kind else None,
                "not_null": any(isinstance(k, exp.NotNullColumnConstraint) for k in kinds),
            })
            for kind in kinds:
                if isinstance(kind, exp.PrimaryKeyColumnConstraint):
                    table["primary_key"].append(node.name)
                elif isinstance(kind, exp.Reference):
                    table["foreign_keys"].append(_foreign_key([node.name], kind))
        elif isinstance(node, exp.Constraint):
            constraints.extend(node.expressions)
        else:
            constraints.append(node)

    for node in constraints:
        if isinstance(node, exp.PrimaryKey):
            table["primary_key"] = [c.name for c in node.expressions]
        elif isinstance(node, exp.ForeignKey):
            columns = [c.name for c in node.expressions]
            table["foreign_keys"].append(_foreign_key(columns, node.args["reference"]))
    return table


def parse_database(db_path: str | Path) -> dict:
    with closing(connect_readonly(db_path)) as conn:
        ddls = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    tables = [parse_ddl(ddl) for (ddl,) in ddls]

    # "REFERENCES t" without columns points at t's primary key
    primary_keys = {t["name"]: t["primary_key"] for t in tables}
    for table in tables:
        for fk in table["foreign_keys"]:
            if not fk["ref_columns"]:
                fk["ref_columns"] = primary_keys.get(fk["ref_table"], [])
    return {"database": Path(db_path).name, "tables": tables}


if __name__ == "__main__":
    print(json.dumps(parse_database(sys.argv[1]), indent=2))
