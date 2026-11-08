"""SchemaSage UI entry point: theme, sidebar database picker and navigation."""

import httpx
import streamlit as st

from ui import api_client, theme

st.set_page_config(page_title="SchemaSage", layout="wide")
theme.inject()

PAGES = [
    st.Page("views/home.py", title="Home", default=True),
    st.Page("views/explorer.py", title="Explorer"),
    st.Page("views/chat.py", title="Chat"),
    st.Page("views/pipeline.py", title="Pipeline"),
    st.Page("views/management.py", title="Management"),
]
page = st.navigation(PAGES, position="hidden")

try:
    databases = api_client.request("GET", "/databases")
except httpx.TransportError:
    st.error(api_client.unreachable())
    st.stop()

names = {db["id"]: db["name"] for db in databases}
# st.switch_page drops the picker's widget value in the browser (not in AppTest), so the choice is
# also kept under a plain key, which Streamlit never cleans up.
if st.session_state.get("selected_database") in names:
    st.session_state["database_id"] = st.session_state["selected_database"]
elif st.session_state.get("database_id") not in names:
    st.session_state["database_id"] = next(iter(names), None)


def _remember_database() -> None:
    st.session_state["selected_database"] = st.session_state["database_id"]


with st.sidebar:
    st.markdown("**SchemaSage**")  # not st.title: the page owns the one title
    st.caption("read-only access")
    st.selectbox("Database", list(names), key="database_id", format_func=names.get, on_change=_remember_database)
    for p in PAGES:
        st.page_link(p)
_remember_database()

page.run()
