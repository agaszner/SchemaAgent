"""LLM table documentation: prompt context, one structured-output call, staleness fingerprint."""
import json
import re
from urllib.parse import quote
from typing import Literal

from pydantic import create_model

CERTAINTY = ("certain", "likely", "uncertain")

INSTRUCTIONS = """You document one table of a SQLite database for analysts who will write SQL against it.
You get the table's schema, a data profile (row count; per column the null ratio, distinct count, min, max and most frequent values), sample rows, and the table's relationships to other tables.
Use only this input. Values shown as *** are masked personal data: never guess, reconstruct or repeat them.
summary: 1-3 sentences on what one row represents and what the table is for.
For every column, description: one sentence on its meaning, unit or format, and notable values (for example which formats the ids of a type table stand for).
certainty per column:
- certain: stated by the schema (primary key, declared or accepted relationship) or unambiguous from the name and the values;
- likely: supported by the values or by a proposed relationship, which is an assumption;
- uncertain: a guess the input does not support.
Write in English."""


def _touching(table: dict, relationships: list[dict]) -> list[dict]:
    return [r for r in relationships
            if r["status"] != "rejected" and table["name"] in (r["from_table"], r["to_table"])]


def table_context(table: dict, profile: dict, relationships: list[dict]) -> str:
    """The prompt input. Values come only from the (already masked) profile."""
    return json.dumps({
        "table": table["name"],
        "columns": [{"name": c["name"], "type": c["type"], "not_null": c["not_null"],
                     "primary_key": c["name"] in table["primary_key"]} for c in table["columns"]],
        "row_count": profile["row_count"],
        "profile": profile["columns"],
        "sample_rows": profile["sample_rows"],
        "relationships": [{k: r[k] for k in ("from_table", "from_columns", "to_table", "to_columns",
                                              "source", "status")} for r in _touching(table, relationships)],
    }, ensure_ascii=False, default=str)


def document_table(client, table: dict, profile: dict, relationships: list[dict], *,
                   model: str, effort: str) -> tuple[dict, dict]:
    names = tuple(c["name"] for c in table["columns"])
    ColumnDoc = create_model("ColumnDoc", name=(Literal[names], ...), description=(str, ...),
                             certainty=(Literal[CERTAINTY], ...))
    TableDoc = create_model("TableDoc", summary=(str, ...), columns=(list[ColumnDoc], ...))
    response = client.responses.parse(
        model=model, reasoning={"effort": effort}, instructions=INSTRUCTIONS,
        input=table_context(table, profile, relationships), text_format=TableDoc)
    parsed = response.output_parsed
    if parsed is None:
        raise ValueError(f"The model refused to document table {table['name']}")
    got = {}
    for c in parsed.columns:
        got.setdefault(c.name, {"name": c.name, "description": c.description, "certainty": c.certainty})
    columns = [got.get(n, {"name": n, "description": "", "certainty": "uncertain"}) for n in names]
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return {"summary": parsed.summary, "columns": columns}, usage


def doc_basis(table: dict, relationships: list[dict]) -> dict:
    """Staleness fingerprint; lists only, so it equals itself after a JSON round trip."""
    return {
        "columns": [c["name"] for c in table["columns"]],
        "relationships": sorted([r["from_table"], list(r["from_columns"]), r["to_table"],
                                 list(r["to_columns"]), r["status"]] for r in _touching(table, relationships)),
    }


def markdown_filename(table: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "_", table) + ".md"


def _one_line(text: str) -> str:
    return " ".join(text.splitlines())  # a newline would end the Markdown table row or list item


def _relationship_lines(relationships: list[dict]) -> list[str]:
    return [f"- {r['from_table']}({', '.join(r['from_columns'])}) → {r['to_table']}({', '.join(r['to_columns'])})"
            + (" (assumed)" if r["status"] == "proposed" else "")
            for r in relationships if r["status"] != "rejected"]


def table_markdown(table: dict, doc: dict, relationships: list[dict]) -> str:
    own = [r for r in _touching(table, relationships)]
    fks = {c: f"FK → {r['to_table']}.{t}" + (" (assumed)" if r["status"] == "proposed" else "")
           for r in own if r["from_table"] == table["name"] for c, t in zip(r["from_columns"], r["to_columns"])}
    notes = {c["name"]: c for c in doc["columns"]}
    rows = []
    for c in table["columns"]:
        key = ", ".join(k for k in ("PK" if c["name"] in table["primary_key"] else "", fks.get(c["name"], "")) if k)
        n = notes.get(c["name"], {"certainty": "uncertain", "description": ""})
        rows.append(f"| {c['name']} | {c['type']} | {key} | {n['certainty']} | {_one_line(n['description']).replace('|', '/')} |")
    return "\n".join([f"# {table['name']}", "", doc["summary"], "", f"Status: {doc['status']}", "",
                      "| Column | Type | Key | Certainty | Description |", "| --- | --- | --- | --- | --- |", *rows,
                      "", "## Relationships", "", *(_relationship_lines(own) or ["None."]), ""])


def overview_markdown(database: str, model: dict, docs: dict[str, dict], relationships: list[dict]) -> str:
    tables = [f"- [{t['name']}](tables/{quote(markdown_filename(t['name']))}) — {_one_line(docs[t['name']]['summary'])}"
              for t in model["tables"] if t["name"] in docs]
    return "\n".join([f"# {database}", "", *tables, "", "## Relationships", "",
                      *(_relationship_lines(relationships) or ["None."]), ""])
