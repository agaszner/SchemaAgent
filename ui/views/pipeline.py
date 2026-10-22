import httpx
import streamlit as st

from ui import api_client, markup, widgets
from ui.network import build_network
from ui.theme import theme_type

ACTIVE = ("queued", "running")

db = widgets.require_database()
runs_path = f"/databases/{db['id']}/runs"
runs = api_client.request("GET", runs_path)  # newest first
active = any(r["status"] in ACTIVE for r in runs)

head, action = st.columns([4, 1], vertical_alignment="bottom")
last = next((r["started_at"] for r in runs if r["started_at"]), None)
head.title("Pipeline")
head.caption(f"{db['name']} · last run {markup.clock(last)}" if last else f"{db['name']} · never run")
with action:
    if st.button("Run all steps", key="run:all", type="primary", disabled=active):
        widgets.start_run(db["id"], "all")

model = api_client.model(db["id"])
relationships = api_client.request("GET", f"/databases/{db['id']}/relationships")
markup.show(markup.stats(markup.counts(model, relationships)))


@st.fragment(run_every="2s" if active else None)
def steps() -> None:
    try:
        current = api_client.request("GET", runs_path)
    except httpx.TransportError:  # the backend went away mid-run; fragment reruns skip main.py's guard
        st.error(api_client.unreachable())
        return
    busy = any(r["status"] in ACTIVE for r in current)
    if active and not busy:
        st.rerun()  # the run finished: refresh the stats and the graph too
    markup.show(markup.steps_table(current))
    for column, (step, _, _) in zip(st.columns(len(markup.STEPS)), markup.STEPS):
        with column:
            if st.button(f"Run {step}", key=f"run:{step}", disabled=busy):
                widgets.start_run(db["id"], step)


steps()

graph_column, log_column = st.columns([2, 1])
with graph_column:
    st.subheader("Relationship graph")
    if model:
        st.iframe(build_network(model, relationships, theme_type()).generate_html(), height=460)
        markup.show(markup.legend())
    else:
        st.caption("Run parse to draw the graph.")
with log_column:
    st.subheader("Run log")
    if runs:
        st.code(markup.run_log(runs), language=None)
    else:
        st.caption("No runs yet.")
