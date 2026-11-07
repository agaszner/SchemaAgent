"""Chat: questions about the schema or the data, each answer with its SQL, JOIN steps and tool calls as evidence."""

import json

import httpx
import streamlit as st

from ui import api_client, widgets

NOT_READY = "Run the pipeline first: the chat reads the parsed schema, the profile and the graph."


def md(text: str) -> str:
    """User or model text for Markdown: Streamlit renders $...$ as math, so "$449.46 and $481.45" would garble."""
    return text.replace("$", r"\$")


def side(step: dict, end: str) -> str:
    return ", ".join(f"{step[end + '_table']}.{c}" for c in step[end + "_columns"])


def evidence(ev: dict) -> None:
    if not (ev["tool_calls"] or ev["tables"] or ev["columns"] or ev["path"]):  # e.g. a refusal: nothing to show
        return
    with st.expander("Evidence"):
        if ev["tables"]:
            st.markdown(md(f"Tables: {', '.join(ev['tables'])}"))
        if ev["columns"]:
            st.markdown(md(f"Columns: {', '.join(ev['columns'])}"))
        if ev["unverified"]:
            st.markdown(md(f"Not found in the schema: {', '.join(ev['unverified'])}"))
        if ev["path"]:  # every find_path call of the turn, so steps rather than one path
            st.markdown(md("JOIN steps:\n\n" + "\n".join(
                f"- {side(s, 'from')} → {side(s, 'to')}" + (" (assumed)" if s["status"] == "proposed" else "")
                for s in ev["path"])))
        for call in ev["tool_calls"]:
            args = call["arguments"]
            st.markdown(f"**{call['name']}**")
            st.code(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False), language="json")
            st.code(call["output"], language=None)


def set_feedback(msg: dict, value: str) -> None:
    try:  # 404 when a masking change cleared the log since this page loaded
        api_client.request("PUT", f"/databases/{db['id']}/chat/{msg['id']}/feedback",
                           json={"feedback": None if msg["feedback"] == value else value})
    except api_client.ApiError as err:
        st.error(err.detail)
        return
    st.rerun()


def turn(msg: dict) -> None:
    ev, kind = msg["evidence"], msg["kind"]
    with st.chat_message("user"):
        st.markdown(md(msg["question"]))
    with st.chat_message("assistant"):
        if kind == "refuse":
            st.warning(md(f"Refused: {msg['answer']}"))
        elif kind == "failed":
            st.error(md(msg["answer"]))
        else:
            st.markdown(md(msg["answer"]))
        if kind == "abstain":
            st.info("Not enough information")
        if ev["assumed"]:
            st.warning(md(f"Relies on assumed relationships: {'; '.join(ev['assumed'])}"))
        result = ev["result"]
        if result:  # any kind that ran SQL; before the expander, whose tool-call code blocks come after the SQL
            st.code(ev["sql"], language="sql")
            names = widgets.unique_names(result["columns"])
            st.dataframe([dict(zip(names, row)) for row in result["rows"]] or {n: [] for n in names}, hide_index=True)
            note = [f"First {len(result['rows']):,} rows"] if result["limited"] else []
            if result["masked"]:
                note.append(f"masked: {', '.join(dict.fromkeys(result['masked']))}")
            if note:
                st.caption(" · ".join(note))
        evidence(ev)
        tokens = msg["input_tokens"] + msg["output_tokens"]
        st.caption(f"{msg['latency_s']:.1f} s · {tokens:,} tokens · ${msg['cost_usd']:.4f}")
        up, down, _ = st.columns([1, 1, 10])
        for col, value, label in ((up, "up", "👍"), (down, "down", "👎")):
            if col.button(label, key=f"chat:{msg['id']}:{value}", type="primary" if msg["feedback"] == value else "secondary"):
                set_feedback(msg, value)


db = widgets.require_database()
st.title("Chat")
st.caption(f"{db['name']} · answers show their SQL, JOIN steps and tool calls")
if api_client.model(db["id"]) is None:  # GET /chat answers [] before the pipeline; the POST would 409
    st.info(NOT_READY)
    st.stop()

for message in api_client.request("GET", f"/databases/{db['id']}/chat"):
    turn(message)

if question := st.chat_input("Ask about the schema or the data"):
    try:  # one turn can take several model rounds
        with st.spinner("Thinking…"):
            api_client.request("POST", f"/databases/{db['id']}/chat", json={"question": question}, timeout=180)
    except httpx.TransportError:  # e.g. a read timeout; the API still logs the turn when the model finishes
        st.info("Still answering; reload the page in a moment to see the answer.")
    except api_client.ApiError as err:
        if err.status == 409:  # parsed, but the profile or the graph is missing
            st.info(NOT_READY)
        else:
            st.error(err.detail)
    else:
        st.rerun()
