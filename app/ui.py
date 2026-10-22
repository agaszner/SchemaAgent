"""Display maps and HTML builders. Builders pass every backend string through html.escape."""

from datetime import datetime
from html import escape

import streamlit as st

from app import theme

STEPS: list[tuple[str, str, str]] = [
    ("parse", "Parse", "DDL → JSON (sqlglot)"),
    ("profile", "Profile", "statistics, sample rows, masking"),
    ("graph", "Graph", "declared and value-based relationships"),
]

RUN_LABEL: dict[str | None, tuple[str, str]] = {
    "done": ("done", "ok"),
    "running": ("running", "run"),
    "queued": ("queued", "idle"),
    "error": ("failed", "error"),
    None: ("not run", "idle"),
}


def run_label(run: dict | None) -> tuple[str, str]:
    """Word and tag kind for a run; a done run whose output masking cleared shows as cleared."""
    if run is None:
        return RUN_LABEL[None]
    if run["status"] == "done" and not run["has_result"]:
        return ("cleared", "warn")
    return RUN_LABEL[run["status"]]


def latest_runs(runs: list[dict]) -> dict[str, dict]:
    """Newest run per step; runs come newest first, as GET /runs returns them."""
    return {run["step"]: run for run in reversed(runs)}


def clock(ts: str | None, fmt: str = "%H:%M") -> str:
    """A backend UTC timestamp in the server's local time."""
    return datetime.fromisoformat(ts).astimezone().strftime(fmt) if ts else "—"


def seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} s"


def tag(word: str, kind: str) -> str:
    return f'<span class="ss-tag ss-tag--{kind}">{escape(word)}</span>'


def ident(name: str) -> str:
    return f'<span class="ss-ident">{escape(name)}</span>'


def counts(model: dict | None, relationships: list[dict]) -> list[tuple[str, str, str]]:
    """StatStrip items: label, value, small text."""
    values = ["—"] * 3
    if model:
        tables = model["tables"]
        found = len(tables), sum(len(t["columns"]) for t in tables), sum(len(t["foreign_keys"]) for t in tables)
        values = [f"{n:,}" for n in found]
    inferred = sum(r["source"] == "inferred" and r["status"] != "rejected" for r in relationships)
    proposed = sum(r["status"] == "proposed" for r in relationships)
    items = [(label, value, "") for label, value in zip(("Tables", "Columns", "Declared FKs"), values)]
    return [*items, ("Inferred edges", f"{inferred:,}", f"/ {proposed:,} to review")]


def stats(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}{f' <small>{escape(small)}</small>' if small else ''}</dd></div>"
        for label, value, small in items)
    return f'<dl class="ss-stats">{cells}</dl>'


def steps_table(runs: list[dict]) -> str:
    """PipelineSteps ledger: one row per STEPS entry with its newest run."""
    latest = latest_runs(runs)
    rows = []
    for step, name, description in STEPS:
        run = latest.get(step)
        error = f'<span class="err">{escape(run["error"])}</span>' if run and run["error"] else ""
        running = ' class="is-running"' if run and run["status"] == "running" else ""
        rows.append(
            f'<tr{running}><td class="step">{escape(name)}<small>{escape(description)}</small>{error}</td>'
            f"<td>{tag(*run_label(run))}</td>"
            f'<td class="num">{clock(run and run["started_at"])}</td>'
            f'<td class="num">{seconds(run and run["duration_s"])}</td></tr>')
    head = '<tr><th>Step</th><th>Status</th><th class="num">Last run</th><th class="num">Duration</th></tr>'
    return f'<table class="ss-steps"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def run_log(runs: list[dict]) -> str:
    """Plain text for st.code: one line per run, newest first."""
    lines = []
    for run in runs:
        parts = [clock(run["started_at"], "%H:%M:%S"), run["step"], run_label(run)[0], seconds(run["duration_s"])]
        if run["error"]:
            parts.append(run["error"])
        lines.append("  ".join(parts))
    return "\n".join(lines)


def legend() -> str:
    """EdgeLegend: the strokes app.graph draws."""

    def stroke(token: str, width: int, dashed: bool = False) -> str:
        # st.html's sanitizer drops <svg>, so the design system's line is a span: solid, or 6 on / 4 off
        paint = f"repeating-linear-gradient(90deg, var(--{token}) 0 6px, transparent 6px 10px)" if dashed else f"var(--{token})"
        return f'<span aria-hidden="true" style="flex:none;width:32px;height:{width}px;background:{paint}"></span>'

    items = [f"<li>{stroke('edge-declared', 2)}declared FK</li>",
             f"<li>{stroke('edge-accepted', 3)}manual / accepted</li>",
             f"<li>{stroke('edge-inferred', 2, dashed=True)}suggested</li>",
             '<li class="ss-faint">rejected: hidden</li>']
    return f'<ul class="ss-legend">{"".join(items)}</ul>'


def show(html: str) -> None:
    st.html(f'<div class="ss" data-theme="{theme.theme_type()}">{html}</div>')


def item(word: str, kind: str, title: str, detail: str) -> str:
    """One Home "Needs review" entry: tag, bold title, muted detail."""
    return f'<div>{tag(word, kind)} <b>{escape(title)}</b><div class="ss-meta">{escape(detail)}</div></div>'


STATUS_LABEL: dict[str, tuple[str, str]] = {
    "proposed": ("suggested", "idle"),
    "accepted": ("accepted", "ok"),
    "rejected": ("rejected", "error"),
}


def ref(table: str, columns: list[str]) -> str:
    """Plain text for one end of a relationship: Table.Col or Table.A, B."""
    return f"{table}.{', '.join(columns)}"


def source_tag(source: str) -> str:
    return f'<span class="ss-tag ss-src ss-src--{escape(source)}">{escape(source)}</span>'


def notice(kind: str, word: str, text: str) -> str:
    """Abstention markup; kind is warn or refused."""
    word_tag = tag(word, "error" if kind == "refused" else "warn")
    return f'<div class="ss-notice ss-notice--{kind}" role="status">{word_tag}<p>{escape(text)}</p></div>'


def relationship(rel: dict, change: dict | None) -> str:
    """RelationshipRow without its actions (those are st.buttons); change is the row's newest log entry."""
    facts = [source_tag(rel["source"]), tag(*STATUS_LABEL[rel["status"]])]
    if rel["source"] == "inferred":
        conf = rel["confidence"]
        facts.append(f'<span class="ss-conf">{conf:.2f}<i><b style="width:{conf:.0%}"></b></i></span>')
    facts += [f"<span>{escape(value)}</span>" for value in (rel["cardinality"], rel["note"]) if value]
    if change:
        what = {"create": "added", "delete": "deleted"}.get(change["action"], change["detail"])
        when = clock(change["at"], "%Y-%m-%d %H:%M")
        facts.append(f'<span>{escape(what)} · {escape(change["actor"])} · {when}</span>')
    edge = (f'{ident(ref(rel["from_table"], rel["from_columns"]))}<span class="ss-faint">→</span>'
            f'{ident(ref(rel["to_table"], rel["to_columns"]))}')
    rejected = " is-rejected" if rel["status"] == "rejected" else ""
    return (f'<div class="ss-rel{rejected}"><div class="ss-rel__edge">{edge}</div>'
            f'<div class="ss-rel__facts">{"".join(facts)}</div></div>')


def match_check(rel: dict, check: dict) -> str:
    """MatchCheck: the POST /relationships/check result for a relationship the form has not saved yet."""
    source, target = ref(rel["from_table"], rel["from_columns"]), ref(rel["to_table"], rel["to_columns"])
    types = " → ".join(", ".join(t or "—" for t in check[side]) for side in ("from_types", "to_types"))
    rows = [("Relationship", f'{ident(source)} → {ident(target)}'),
            ("Types", escape(types)),
            ("Match", escape(f"{check['coverage']:.0%} of {source} values exist in {target}."))]
    cells = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    warnings = "".join(f"<p>{escape(w)}</p>" for w in check["warnings"])
    status = tag("warning", "warn") if check["warnings"] else tag("ok", "ok")
    return (f'<section class="ss-panel" aria-label="Value match"><header><h3>Value match</h3>{status}</header>'
            f'<div class="ss-panel__body"><dl class="ss-dl">{cells}</dl>{warnings}</div></section>')
