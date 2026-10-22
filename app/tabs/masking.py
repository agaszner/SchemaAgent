"""Management · Masking: name-pattern and user-marked sensitive columns."""

import streamlit as st

from app import api
from app.ui import ident, notice, show
from app.widgets import start_run

STALE = ("The profile is missing or was cleared by a masking change. "
         "Name-pattern columns are listed again after the Profile step runs.")


def _label(column: dict) -> str:
    return f"{column['table']}.{column['column']}"


def _row(label: str, detected_by: str, editable: bool) -> bool:
    name, kind, toggle = st.columns([3, 2, 1])
    with name:
        show(ident(label))
    kind.write(detected_by)
    with toggle:
        return st.toggle("Masked", value=True, key=f"mask:{label}", disabled=not editable,
                         label_visibility="collapsed")


def _save(database_id: int, columns: list[dict], marked: list[dict]) -> None:
    if {(c["table"], c["column"]) for c in columns} == {(c["table"], c["column"]) for c in marked}:
        st.info("No changes to save.")  # every PUT deletes the profile, so an unchanged one is skipped
        return
    try:
        api.request("PUT", f"/databases/{database_id}/masking", json={"columns": columns})
    except api.ApiError as err:
        st.error(err.detail)
        return
    st.toast("Saved")
    st.rerun()


def render(db: dict | None) -> None:
    if db is None:
        st.info("Select a database in the sidebar.")
        return
    model = api.model(db["id"])
    if model is None:
        st.info("Run parse first: masking applies to the parsed schema.")
        return
    profile = api.profile(db["id"])
    marked = db["masked_columns"]
    marked_keys = {(c["table"], c["column"]) for c in marked}
    pattern = [] if profile is None else [
        {"table": t["table"], "column": c["name"]} for t in profile["tables"] for c in t["columns"]
        if c["masked"] and (t["table"], c["name"]) not in marked_keys]

    st.subheader("Sensitive columns")
    st.caption(f"{len(pattern) + len(marked)} masked")
    st.write("Values in masked columns are replaced in the profiler, before any LLM call. "
             "The LLM never sees the originals.")
    if profile is None:
        show(notice("warn", "profile needed", STALE))
        if st.button("Run profile", key="mask_run_profile"):
            start_run(db["id"], "profile")

    listed = {_label(c) for c in pattern + marked}
    addable = {f"{t['name']}.{c['name']}": {"table": t["name"], "column": c["name"]}
               for t in model["tables"] for c in t["columns"]}
    addable = {k: v for k, v in addable.items() if k not in listed}
    with st.form("masking_form"):
        for c in pattern:
            _row(_label(c), "name pattern", editable=False)
        kept = [c for c in marked if _row(_label(c), "marked by you", editable=True)]
        added = st.multiselect("Add columns", key="mask_add", options=list(addable))
        if st.form_submit_button("Save masking", key="mask_save", type="primary"):
            _save(db["id"], kept + [addable[a] for a in added], marked)
