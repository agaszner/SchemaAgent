import httpx
import streamlit as st

from app import api, ui, widgets
from app.graph import build_network
from app.theme import theme_type

ACTIVE = ("queued", "running")

db = widgets.require_database()
runs_path = f"/databases/{db['id']}/runs"
runs = api.request("GET", runs_path)  # newest first
active = any(r["status"] in ACTIVE for r in runs)

head, action = st.columns([4, 1], vertical_alignment="bottom")
last = next((r["started_at"] for r in runs if r["started_at"]), None)
head.title("Pipeline")
head.caption(f"{db['name']} · last run {ui.clock(last)}" if last else f"{db['name']} · never run")
with action:
    if st.button("Run all steps", key="run:all", type="primary", disabled=active):
        widgets.start_run(db["id"], "all")

model = api.model(db["id"])
relationships = api.request("GET", f"/databases/{db['id']}/relationships")
ui.show(ui.stats(ui.counts(model, relationships)))


@st.fragment(run_every="2s" if active else None)
def steps() -> None:
    try:
        current = api.request("GET", runs_path)
    except httpx.TransportError:  # the backend went away mid-run; fragment reruns skip main.py's guard
        st.error(api.unreachable())
        return
    busy = any(r["status"] in ACTIVE for r in current)
    if active and not busy:
        st.rerun()  # the run finished: refresh the stats and the graph too
    ui.show(ui.steps_table(current))
    for column, (step, _, _) in zip(st.columns(len(ui.STEPS)), ui.STEPS):
        with column:
            if st.button(f"Run {step}", key=f"run:{step}", disabled=busy):
                widgets.start_run(db["id"], step)


steps()

graph_column, log_column = st.columns([2, 1])
with graph_column:
    st.subheader("Relationship graph")
    if model:
        st.iframe(build_network(model, relationships, theme_type()).generate_html(), height=460)
        ui.show(ui.legend())
    else:
        st.caption("Run parse to draw the graph.")
with log_column:
    st.subheader("Run log")
    if runs:
        st.code(ui.run_log(runs), language=None)
    else:
        st.caption("No runs yet.")
