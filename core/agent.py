"""Chat agent (spec §2): a tool-calling loop on the Responses API over the docs, the graph, samples and guarded SQL.

The model decides what it says; the turn's evidence (SQL, result, JOIN path, assumed relationships) comes from the
tool trace.
"""

import json
import re
import sqlite3
import time
from typing import Literal

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from core.documentation import table_markdown
from core.graph import find_path
from core.rows import read_rows
from core.sql_guard import GuardError, run_sql

MAX_ROUNDS = 8
HISTORY_TURNS = 3
SAMPLE_LIMIT = 10

INSTRUCTIONS = """\
You answer questions about one SQLite database. Use the tools; never invent tables, columns, relationships or values.
Schema questions (where something is stored, how tables relate, how to join them): read get_table_doc and find_path, set kind "schema", and name the tables and the columns (as Table.Column) your answer relies on.
Data questions: write one SQLite SELECT, run it with run_sql, and set kind "data"; answer states the result in one or two sentences. Use get_sample_rows or the profile for exact values (for example the spelling of a genre name).
If run_sql fails, you get your SQL and the error: fix the query once. If it fails again, stop and say so.
Relationships with status "proposed" are assumptions: say so whenever your answer relies on one.
Values shown as *** are masked personal data: never guess or reconstruct them.
If the database does not hold what the question needs, set kind "abstain" and say what is missing.
If the user asks to change data or the schema (INSERT, UPDATE, DELETE, CREATE, DROP, ALTER), set kind "refuse" without calling any tool: you have read-only access.
Answer in the language of the question."""


def _tool(name: str, description: str, **params: str) -> dict:
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": {k: {"type": t} for k, t in params.items()},
                           "required": list(params), "additionalProperties": False}}


TOOLS = [
    _tool("search_tables", "Tables whose name, columns or documentation mention a word of the query; "
          "all tables with their summaries when nothing matches.", query="string"),
    _tool("get_table_doc", "A table's documentation (columns, keys, relationships) and its data profile.",
          table="string"),
    _tool("find_path", "The JOIN path between two tables, each step with its relationship status.",
          from_table="string", to_table="string"),
    _tool("get_sample_rows", f"Up to {SAMPLE_LIMIT} rows of a table; personal data is masked as ***.",
          table="string", limit="integer"),
    _tool("run_sql", "Run one read-only SQLite SELECT; returns the columns and rows.", sql="string"),
]

UNDOCUMENTED = {"summary": "(not documented yet)", "columns": [], "status": "missing"}


class AgentAnswer(BaseModel):  # strict structured outputs: every field required, no defaults
    kind: Literal["data", "schema", "abstain", "refuse"]
    answer: str
    tables: list[str]
    columns: list[str]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def answer_question(client, question: str, sources: dict, *, history: list[dict], db_path, marks: list[tuple[str, str]],
                    llm: dict) -> dict:
    """One chat turn: the tool loop, then the turn dict with the evidence taken from the trace."""
    start = time.perf_counter()
    model = sources["model"]
    by_name = {t["name"].lower(): t for t in model["tables"]}
    tokens = {"input_tokens": 0, "output_tokens": 0}
    trace, path = [], []
    state = {"failures": 0, "last": None}

    def resolve(name: str) -> dict:
        if name.lower() not in by_name:
            raise LookupError(f"unknown table {name}; tables: {', '.join(t['name'] for t in model['tables'])}")
        return by_name[name.lower()]

    def doc(name: str) -> dict:
        return sources["docs"].get(name, UNDOCUMENTED)

    def search_tables(query: str) -> str:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]
        hits, every = [], []
        for t in model["tables"]:
            d = doc(t["name"])
            text = " ".join([t["name"], d["summary"], *(c["name"] for c in t["columns"]),
                             *(c["description"] for c in d["columns"])]).lower()
            every.append({"table": t["name"], "summary": d["summary"]})
            if any(w in text for w in words):
                hits.append({"table": t["name"], "summary": d["summary"], "columns": [c["name"] for c in t["columns"]]})
        return _json(hits or every)

    def get_table_doc(table: str) -> str:
        t = resolve(table)
        profile = next((p for p in sources["profile"]["tables"] if p["table"] == t["name"]), None)
        return table_markdown(t, doc(t["name"]), sources["relationships"]) + "\n## Profile\n\n" + _json(profile)

    def find(from_table: str, to_table: str) -> str:
        a, b = resolve(from_table)["name"], resolve(to_table)["name"]
        steps = find_path(sources["graph"], a, b)
        if steps is None:
            return f"no path between {a} and {b}"
        path.extend(s for s in steps if s not in path)
        return _json(steps)

    def get_sample_rows(table: str, limit: int) -> str:
        t = resolve(table)
        return _json(read_rows(db_path, t, [c for n, c in marks if n == t["name"]],
                               limit=min(max(limit, 1), SAMPLE_LIMIT))["rows"])

    def sql_tool(sql: str) -> str:
        if state["failures"] >= 2:
            return "run_sql is no longer available in this turn: say that the query could not be run."
        try:
            result = run_sql(db_path, sql, model, marks, max_rows=llm["max_rows"], timeout_s=llm["timeout_s"])
        except GuardError as err:
            state["failures"] += 2 if err.kind == "database" else 1  # no rewrite of the query helps a database error
            return f"{sql}\n{err.kind}: {err}"
        state["last"] = result
        return _json({k: result[k] for k in ("columns", "rows", "masked", "limited")})

    tools = {"search_tables": search_tables, "get_table_doc": get_table_doc, "find_path": find,
             "get_sample_rows": get_sample_rows, "run_sql": sql_tool}

    messages = [m for turn in history[-HISTORY_TURNS:] for m in ({"role": "user", "content": turn["question"]},
                                                                  {"role": "assistant", "content": turn["answer"]})]
    messages.append({"role": "user", "content": question})
    for _ in range(MAX_ROUNDS):
        response = client.responses.parse(model=llm["model"], reasoning={"effort": llm["effort"]},
                                          instructions=INSTRUCTIONS, input=messages, tools=TOOLS,
                                          text_format=AgentAnswer)
        tokens["input_tokens"] += response.usage.input_tokens
        tokens["output_tokens"] += response.usage.output_tokens
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            parsed = response.output_parsed
            answer = (parsed.model_dump() if parsed is not None else
                      {"kind": "refuse", "answer": "The model refused to answer.", "tables": [], "columns": []})
            break
        messages.extend(response.output)  # reasoning items must stay next to their calls
        for call in calls:
            arguments = call.arguments
            try:
                arguments = json.loads(call.arguments)
                if call.name not in tools:
                    raise LookupError(f"unknown tool {call.name}; tools: {', '.join(tools)}")
                output = tools[call.name](**arguments)
            except (LookupError, ValueError, TypeError, sqlite3.Error) as err:  # bad call, unknown table, unreadable file
                output = str(err)
            trace.append({"name": call.name, "arguments": arguments, "output": output})
            messages.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
    else:
        answer = {"kind": "failed", "answer": f"No answer after {MAX_ROUNDS} rounds of tool calls.",
                  "tables": [], "columns": []}

    last = state["last"]
    if answer["kind"] == "data" and last is None:
        answer["kind"] = "failed"
        answer["answer"] = "No query ran successfully, so this answer is unverified: " + answer["answer"]
    unverified = []
    if answer["kind"] == "schema":  # data answers list SQL aliases and expressions, as in evaluation._claims
        known = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in model["tables"]}
        anywhere = set().union(*known.values())
        unverified = [t for t in answer["tables"] if t.lower() not in known]
        for col in answer["columns"]:
            t, _, name = col.lower().rpartition(".")
            if name not in known[t] if t in known else name not in anywhere:  # unknown prefix: maybe an alias
                unverified.append(col)

    def side(s: dict, end: str) -> str:
        return ", ".join(f"{s[end + '_table']}.{c}" for c in s[end + "_columns"])

    assumed = [s for s in path if s["status"] == "proposed"]
    if last:  # a proposed relationship between two tables the query read; over-flags a query that never joins them
        used = {t.name.lower() for t in sqlglot.parse_one(last["sql"], read="sqlite").find_all(exp.Table)}
        assumed += [r for r in sources["relationships"] if r["status"] == "proposed"
                    and r["from_table"].lower() in used and r["to_table"].lower() in used]
    return {
        **answer, "unverified": unverified, "path": path,
        "assumed": list(dict.fromkeys(f"{side(s, 'from')} → {side(s, 'to')}" for s in assumed)),
        "sql": last["sql"] if last else None,
        "result": {k: last[k] for k in ("columns", "rows", "masked", "limited")} if last else None,
        "tool_calls": trace, **tokens,
        "cost_usd": (tokens["input_tokens"] * llm["input_usd_per_mtok"]
                     + tokens["output_tokens"] * llm["output_usd_per_mtok"]) / 1e6,
        "latency_s": time.perf_counter() - start,
    }
