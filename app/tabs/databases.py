"""Management · Databases: the registered databases, upload and register forms."""

import streamlit as st

from app import api
from app.widgets import databases_table


def _submit(path: str, **kwargs) -> None:
    """POST to the backend; an error shows inside the calling form."""
    try:
        api.request("POST", path, **kwargs)
    except api.ApiError as err:
        st.error(err.detail)
        return
    st.toast("Saved")
    st.rerun()


def render(db: dict | None) -> None:
    st.subheader("Registered databases")
    st.caption(f"{len(api.request('GET', '/databases'))} · all opened read-only")
    databases_table()
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
