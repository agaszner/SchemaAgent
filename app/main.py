"""SchemaSage UI entry point: theme, sidebar database picker and navigation."""

import httpx
import streamlit as st

from app import api, theme

st.set_page_config(page_title="SchemaSage", layout="wide")
theme.inject()

PAGES = [
    st.Page("views/home.py", title="Home", default=True),
    st.Page("views/pipeline.py", title="Pipeline"),
    st.Page("views/management.py", title="Management"),
]
page = st.navigation(PAGES, position="hidden")

try:
    databases = api.request("GET", "/databases")
except httpx.TransportError:
    st.error(api.unreachable())
    st.stop()

names = {db["id"]: db["name"] for db in databases}
if st.session_state.get("database_id") not in names:
    st.session_state["database_id"] = next(iter(names), None)

with st.sidebar:
    st.markdown("**SchemaSage**")  # not st.title: the page owns the one title
    st.caption("read-only access")
    st.selectbox("Database", list(names), key="database_id", format_func=names.get)
    for p in PAGES:
        st.page_link(p)

page.run()
