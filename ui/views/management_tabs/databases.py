"""Management · Databases: the registered databases, upload and register forms."""

import streamlit as st

from ui import api_client
from ui.widgets import databases_table


def _submit(path: str, **kwargs) -> None:
    """POST to the backend; an error shows inside the calling form."""
    try:
        api_client.request("POST", path, **kwargs)
    except api_client.ApiError as err:
        st.error(err.detail)
        return
    st.toast("Saved")
    st.rerun()


def _remove(databases: list[dict]) -> None:
    """Pick a database, press Remove, then confirm: removal drops its review work from app.db."""
    names = {d["id"]: d["name"] for d in databases}
    pick, button = st.columns([3, 1], vertical_alignment="bottom")
    target = pick.selectbox("Remove a database", list(names), format_func=names.get, key="remove_target")
    if st.session_state.get("remove_pending") != target:
        if button.button("Remove", key="remove"):
            st.session_state["remove_pending"] = target
            st.rerun()
        return
    st.warning(f"Removing {names[target]} deletes its runs, docs, relationship decisions, change log and chat log "
               "from app.db. The file stays on disk, so you can register it again.")
    confirm, cancel, _ = st.columns([1, 1, 4])
    if confirm.button("Remove", key="remove_confirm", type="primary"):
        try:
            api_client.request("DELETE", f"/databases/{target}")
        except api_client.ApiError as err:
            st.error(err.detail)
            return
        st.session_state.pop("remove_pending", None)
        st.toast(f"Removed {names[target]}")
        st.rerun()
    if cancel.button("Cancel", key="remove_cancel"):
        st.session_state.pop("remove_pending", None)
        st.rerun()


def render(db: dict | None) -> None:
    databases = api_client.request("GET", "/databases")
    st.subheader("Registered databases")
    st.caption(f"{len(databases)} · all opened read-only")
    databases_table()
    if databases:
        _remove(databases)
    upload, register = st.columns(2, border=True)
    with upload, st.form("upload_form", clear_on_submit=True):
        st.markdown("**Upload a file**")
        file = st.file_uploader("Database file", type=["db", "sqlite", "sqlite3"], key="upload_file")
        st.caption("SQLite only · up to 200 MB · stored in the data folder under uploads/")
        if st.form_submit_button("Upload", key="upload") and file is not None:
            _submit("/databases/upload", files={"file": (file.name, file.getvalue())})
    with register, st.form("register_form"):
        st.markdown("**Register a path**")
        path = st.text_input("Path", key="register_path", placeholder="data/sakila.db")
        name = st.text_input("Display name", key="register_name", placeholder="defaults to the file name")
        st.caption("The path is resolved on the server and must be inside the data folder.")
        if st.form_submit_button("Register", key="register", type="primary"):
            _submit("/databases", json={"path": path, "name": name or None})
    st.caption("SchemaSage opens registered databases read-only. Run history and review decisions live in app.db.")
