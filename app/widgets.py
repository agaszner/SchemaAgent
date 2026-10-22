"""Streamlit pieces shared by several pages."""

import streamlit as st

from app import api, ui


def current_database() -> dict | None:
    """DatabaseOut of the selected id, or None when unset or gone."""
    database_id = st.session_state.get("database_id")
    if database_id is None:
        return None
    try:
        return api.request("GET", f"/databases/{database_id}")
    except api.ApiError as err:
        if err.status == 404:
            return None
        raise


def require_database() -> dict:
    db = current_database()
    if db is None:
        st.info("No database registered yet. Register one in Management · Databases.")
        if st.button("Manage databases", key="manage_databases"):  # Home and Pipeline only, never Management
            st.switch_page("views/management.py", query_params={"tab": "Databases"})
        st.stop()
    return db


def databases_table() -> None:
    # ponytail: two requests per database; fine for a handful of databases
    rows = []
    for db in api.request("GET", "/databases"):
        model = api.model(db["id"])
        runs = api.request("GET", f"/databases/{db['id']}/runs")  # newest first
        tables = model["tables"] if model else None
        rows.append({
            "Database": db["name"],
            "Tables": len(tables) if model else None,
            "Columns": sum(len(t["columns"]) for t in tables) if model else None,
            "Declared FKs": sum(len(t["foreign_keys"]) for t in tables) if model else None,
            "Pipeline": ui.run_label(runs[0] if runs else None)[0],
        })
    counts = ("Tables", "Columns", "Declared FKs")  # float once a None is present; show as integers
    st.dataframe(rows, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%d") for c in counts})


def start_run(database_id: int, step: str) -> None:
    """Start a pipeline run; a refusal (missing prerequisite, run in progress) shows beside the button."""
    try:
        api.request("POST", f"/databases/{database_id}/runs", json={"step": step})
    except api.ApiError as err:
        st.error(err.detail)
        return
    st.rerun()
