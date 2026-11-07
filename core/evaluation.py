"""Benchmark (spec §7): the question set and gold SQL loaders, the A/B/C context builders, asking and scoring."""

import json
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from core.graph import find_path
from core.sql_guard import GuardError, run_sql
from core.sqlite import connect_readonly


def load_questions(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_gold(path: str | Path) -> dict[str, str]:
    """`-- <id>` header lines, each followed by one SQL statement; returns id -> SQL without the trailing `;`."""
    blocks = re.split(r"^-- ([a-z]*[sd]\d{2})$", Path(path).read_text(encoding="utf-8"), flags=re.M)[1:]
    gold = {}
    for qid, sql in zip(blocks[::2], blocks[1::2]):
        if qid in gold:
            raise ValueError(f"gold id {qid} appears twice")
        gold[qid] = sql.strip().rstrip(";").strip()
    return gold


CONFIGS = ("A", "B", "C")


def raw_ddl(db_path: str | Path) -> str:
    with connect_readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return "\n\n".join(r[0].rstrip().rstrip(";") + ";" for r in rows if r[0])


def mentioned_tables(question: str, model: dict) -> list[str]:
    """Model tables named in the question, by first position. Longest forms match first and consume their span."""
    forms = []
    for t in model["tables"]:
        name = t["name"]
        words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).split()
        for form in {name, " ".join(words)}:
            forms.append((form, name))
    forms.sort(key=lambda f: -len(f[0]))
    taken, found = [], {}
    for form, name in forms:
        pattern = r"\b" + r"\s+".join(map(re.escape, form.split())) + r"s?\b"
        for m in re.finditer(pattern, question, re.I):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append(m.span())
            found[name] = min(found.get(name, m.start()), m.start())
    return sorted(found, key=found.get)


def _step(rel: dict, arrow: str) -> str:
    left = ", ".join(f"{rel['from_table']}.{c}" for c in rel["from_columns"])
    right = ", ".join(f"{rel['to_table']}.{c}" for c in rel["to_columns"])
    return f"{left} {arrow} {right} ({'assumed' if rel['status'] == 'proposed' else rel['status']})"


def _tables_section(sources: dict) -> str:
    out = ["## Tables"]
    for t in sources["model"]["tables"]:
        doc = sources["docs"].get(t["name"], {})
        fks = {}  # accepted sorts last, so it wins over a proposed one on the same column
        for r in sorted(sources["relationships"], key=lambda r: r["status"] == "accepted"):
            if r["from_table"] == t["name"] and r["status"] != "rejected":
                for c, rc in zip(r["from_columns"], r["to_columns"]):
                    fks[c] = f"{r['to_table']}.{rc}" + (" (assumed)" if r["status"] == "proposed" else "")
        described = {c["name"]: c for c in doc.get("columns", [])}
        out += ["", t["name"], doc.get("summary", "")]
        for col in t["columns"]:
            d = described.get(col["name"], {})
            line = f"- {col['name']} {col['type']}"
            line += " PK" if col["name"] in t["primary_key"] else ""
            line += f" FK → {fks[col['name']]}" if col["name"] in fks else ""
            line += f" ({d['certainty']}): {d['description']}" if d else ""
            out.append(line)
    return "\n".join(out)


def build_context(config: str, question: str, sources: dict) -> str:
    """Context for one configuration; the question is not part of it."""
    if config == "A":
        return "## Schema (DDL)\n" + sources["ddl"]
    if config not in ("B", "C"):
        raise ValueError(f"unknown config {config!r}")
    parts = [_tables_section(sources)]
    rels = [r for r in sources["relationships"] if r["status"] != "rejected"]
    parts.append("## Relationships\n" + "\n".join(_step(r, "->") for r in rels))
    names = mentioned_tables(question, sources["model"])
    steps = []
    for a, b in zip(names, names[1:]):
        here = a
        for step in find_path(sources["graph"], a, b) or []:
            if step["from_table"] != here:  # print each step in walking direction
                step = {**step, "from_table": step["to_table"], "from_columns": step["to_columns"],
                        "to_table": step["from_table"], "to_columns": step["from_columns"]}
            here = step["to_table"]
            steps.append(step)
    if config == "C":
        parts.append("## Data profile\n" + "\n".join(
            f"{t['table']}: {json.dumps(t, ensure_ascii=False, separators=(',', ':'), default=str)}"
            for t in sources["profile"]["tables"]))
    if len(names) >= 2 and steps:  # last: everything before it is the same for every question, so prompt caching reuses it
        parts.append("## JOIN path\n" + "\n".join(_step(s, "=") for s in steps))
    return "\n\n".join(parts)


INSTRUCTIONS = """\
You answer questions about one SQLite database using only the context you are given.
For a question about the data, set kind "sql" and write one SQLite SELECT statement that answers it in sql; answer says in one sentence what it computes.
For a question about the schema (where something is stored, how tables relate, how to join them), set kind "schema", answer in one or two sentences, list the tables and the columns (as Table.Column) your answer relies on, and when the question asks how tables connect, put the tables in join order in path.
If the context does not contain what the question needs, set kind "abstain" and say in answer what is missing. Never invent tables, columns or values.
Relationships marked (assumed) are proposals, not facts: say so in answer when you rely on one.
Always fill every field: sql is null unless kind is "sql"; use empty lists when nothing applies."""


class Answer(BaseModel):  # strict structured outputs: every field required, no defaults
    kind: Literal["sql", "schema", "abstain"]
    sql: str | None
    answer: str
    tables: list[str]
    columns: list[str]
    path: list[str]


def ask(client, context: str, question: str, *, model: str, effort: str,
        repair: str | None = None) -> tuple[dict, dict]:
    text = context + "\n\n## Question\n" + question
    if repair is not None:
        text += "\n\n## Your previous SQL failed\n" + repair
    response = client.responses.parse(model=model, reasoning={"effort": effort}, instructions=INSTRUCTIONS,
                                      input=text, text_format=Answer)
    if response.output_parsed is None:
        raise ValueError("The model refused to answer the question")
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return response.output_parsed.model_dump(), usage


def _sort_key(v):
    return (v is None, isinstance(v, (str, bytes)), str(v) if isinstance(v, bytes) else v if v is not None else 0)


def results_match(gold_rows: list[list], pred_rows: list[list]) -> bool:
    """Same row count, and every gold column (as a sorted, 2-decimal-rounded list) equals some predicted column."""
    if len(gold_rows) != len(pred_rows):
        return False

    def columns(rows):
        return [sorted((round(v, 2) if isinstance(v, float) else v for v in col), key=_sort_key) for col in zip(*rows)]

    pred = columns(pred_rows)
    return all(g in pred for g in columns(gold_rows))


def _fold_all(names) -> set[str]:
    return {n.lower() for n in names}


def _claims(answer: dict, model: dict) -> tuple[int, int]:
    """(claims, unsupported) over the tables and columns a schema answer names; SQL answers list aliases and expressions."""
    if answer["kind"] != "schema":
        return 0, 0
    tables = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in model["tables"]}
    anywhere = set().union(*tables.values())
    bad = sum(t.lower() not in tables for t in answer["tables"])
    for col in answer["columns"]:
        table, _, name = col.lower().rpartition(".")
        bad += name not in tables[table] if table in tables else name not in anywhere  # unknown prefix: maybe an alias
    return len(answer["tables"]) + len(answer["columns"]), bad


def run_question(client, config: str, question: dict, gold_sql: str | None, sources: dict, *,
                 db_path, marks, llm: dict) -> dict:
    """Ask once, run SQL through the guard with one repair retry, and score; returns one results.csv row."""
    context = build_context(config, question["question"], sources)
    tokens = {"input_tokens": 0, "output_tokens": 0}
    start = time.perf_counter()

    def call(repair=None):
        answer, usage = ask(client, context, question["question"], model=llm["model"], effort=llm["effort"],
                            repair=repair)
        for k in tokens:
            tokens[k] += usage[k]
        return answer

    def execute(sql):
        return run_sql(db_path, sql or "", sources["model"], marks,
                       max_rows=llm["max_rows"], timeout_s=llm["timeout_s"])["rows"]

    answer = call()
    first_sql = answer["sql"]
    unanswerable = question["kind"] == "unanswerable"
    score = path_correct = first_valid = repaired = error = None
    if question["category"] == "data":
        rows = None
        if answer["kind"] == "sql":
            try:
                rows, first_valid = execute(answer["sql"]), True
            except GuardError as err:
                first_valid, error = False, f"{err.kind}: {err}"
                if err.kind != "database":
                    previous = answer["sql"]
                    answer, repaired = call(repair=f"{previous}\n{err.kind}: {err}"), False
                    if answer["kind"] == "sql":
                        try:
                            rows, repaired, error = execute(answer["sql"]), True, None
                        except GuardError as err2:
                            error = f"{err2.kind}: {err2}"
        if unanswerable:
            correct = answer["kind"] == "abstain"
        else:  # gold runs only when there is something to compare; a gold failure is a broken benchmark and raises
            correct = bool(rows) and results_match(execute(gold_sql), rows)
    elif unanswerable:
        correct = answer["kind"] == "abstain"
    else:
        expected = _fold_all(question["expected"]["tables"] + question["expected"].get("columns", []))
        score = len(expected & _fold_all(answer["tables"] + answer["columns"])) / len(expected)
        if question["kind"] in ("relationship", "path"):
            want, got = [t.lower() for t in question["expected"]["path"]], [t.lower() for t in answer["path"]]
            path_correct = got in (want, want[::-1])
        correct = score == 1.0 and path_correct is not False
    latency = time.perf_counter() - start
    claims, unsupported = _claims(answer, sources["model"])
    return {
        "config": config, "id": question["id"], "category": question["category"], "kind": question["kind"],
        "answer_kind": answer["kind"], "correct": correct, "score": score, "path_correct": path_correct,
        "first_valid": first_valid, "repaired": repaired,
        "claims": claims, "unsupported": unsupported, "latency_s": latency, **tokens,
        "cost_usd": (tokens["input_tokens"] * llm["input_usd_per_mtok"]
                     + tokens["output_tokens"] * llm["output_usd_per_mtok"]) / 1e6,
        "answer": answer["answer"], **{k: "; ".join(answer[k]) for k in ("tables", "columns", "path")},  # flat CSV cells
        "first_sql": first_sql, "sql": answer["sql"], "error": error,
    }


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def summarize(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for config in dict.fromkeys(r["config"] for r in rows):
        rs = [r for r in rows if r["config"] == config]
        data = [r for r in rs if r["category"] == "data" and r["kind"] != "unanswerable"]
        retried = [r for r in data if r["repaired"] is not None]
        scores = [r["score"] for r in rs if r["score"] is not None]
        paths = [r["path_correct"] for r in rs if r["path_correct"] is not None]
        out[config] = {
            "n": len(rs),
            "execution_accuracy": _rate(sum(r["correct"] for r in data), len(data)),
            "valid_sql_rate": _rate(sum(r["first_valid"] is True for r in data), len(data)),
            "repair_success": _rate(sum(r["repaired"] for r in retried), len(retried)),
            "schema_fact_accuracy": _rate(sum(scores), len(scores)),
            "path_accuracy": _rate(sum(paths), len(paths)),
            "unsupported_claim_rate": _rate(sum(r["unsupported"] for r in rs), sum(r["claims"] for r in rs)),
            "abstention_accuracy": _rate(sum((r["answer_kind"] == "abstain") == (r["kind"] == "unanswerable")
                                             for r in rs), len(rs)),
            "mean_latency_s": _rate(sum(r["latency_s"] for r in rs), len(rs)),
            "input_tokens": sum(r["input_tokens"] for r in rs),
            "output_tokens": sum(r["output_tokens"] for r in rs),
            "cost_usd": sum(r["cost_usd"] for r in rs),
        }
    return out
