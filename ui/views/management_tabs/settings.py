"""Management · Settings: limits applied to every SQL query."""

import streamlit as st

from ui import api_client


def render(db: dict | None) -> None:  # db unused: the settings are global
    current = api_client.request("GET", "/settings")
    with st.form("settings", border=False):
        max_rows = st.number_input("Row limit", 1, 1000, current["max_rows"], step=1, key="settings:max_rows")
        timeout_s = st.number_input("Query timeout (s)", 0.1, 60.0, current["timeout_s"], step=0.5,
                                    format="%.1f", key="settings:timeout_s")
        st.caption("Every SQL query gets at most this many rows and this much time.")
        if st.form_submit_button("Save", key="settings:save", type="primary"):
            try:
                api_client.request("PUT", "/settings", json={"max_rows": max_rows, "timeout_s": timeout_s})
            except api_client.ApiError as err:
                st.error(err.detail)
            else:
                st.toast("Saved")
