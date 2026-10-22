import streamlit as st

from app import api, ui, widgets

db = widgets.require_database()
runs = api.request("GET", f"/databases/{db['id']}/runs")  # newest first
model = api.model(db["id"])
relationships = api.request("GET", f"/databases/{db['id']}/relationships")

head, action = st.columns([4, 1], vertical_alignment="bottom")
head.title("SchemaSage")
head.caption(f"Database documentation, relationship review and read-only SQL · {db['name']}")
with action:
    active = any(r["status"] in ("queued", "running") for r in runs)
    if st.button("Run all steps", key="run:all", type="primary", disabled=active):
        widgets.start_run(db["id"], "all")

ui.show(ui.stats(ui.counts(model, relationships)))

latest = ui.latest_runs(runs)
failed = next(((name, latest[step]) for step, name, _ in ui.STEPS
               if step in latest and latest[step]["status"] == "error"), None)
proposed = sum(r["status"] == "proposed" for r in relationships)
with st.container(border=True):
    st.subheader("Needs review")
    if proposed:
        text, button = st.columns([5, 1], vertical_alignment="center")
        with text:
            ui.show(ui.item(*ui.STATUS_LABEL["proposed"], f"{proposed} suggested relationships",
                            "Accept or reject them in Management."))
        if button.button("Review", key="review_relationships"):
            st.session_state["tab"] = "Relationships"  # switch_page drops ?tab= when it holds the default tab
            st.switch_page("views/management.py", query_params={"tab": "Relationships"})
    if failed:
        name, run = failed
        text, button = st.columns([5, 1], vertical_alignment="center")
        with text:
            ui.show(ui.item(*ui.run_label(run), f"{name} step failed", run["error"]))
        if button.button("Open pipeline", key="open_pipeline"):
            st.switch_page("views/pipeline.py")
    if not proposed and not failed:
        st.caption("Nothing needs review.")

head, action = st.columns([4, 1], vertical_alignment="bottom")
head.subheader("Databases")
if action.button("Manage databases", key="manage_databases"):
    st.switch_page("views/management.py", query_params={"tab": "Databases"})
widgets.databases_table()
