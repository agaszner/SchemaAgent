"""Management · Settings: limits applied to every SQL query, and the LLM."""

import streamlit as st

from ui import api_client


def render(db: dict | None) -> None:  # db unused: the settings are global
    current = api_client.request("GET", "/settings")
    with st.form("settings", border=False):
        max_rows = st.number_input("Row limit", 1, 1000, current["max_rows"], step=1, key="settings:max_rows")
        timeout_s = st.number_input("Query timeout (s)", 0.1, 60.0, current["timeout_s"], step=0.5,
                                    format="%.1f", key="settings:timeout_s")
        st.caption("Every SQL query gets at most this many rows and this much time.")
        st.subheader("LLM")
        llm_model = st.text_input("Model", current["llm_model"], key="settings:llm_model")
        efforts = ["none", "low", "medium", "high"]
        effort = st.selectbox("Reasoning effort", efforts, efforts.index(current["reasoning_effort"]),
                              key="settings:reasoning_effort")
        input_price = st.number_input("Input $ / 1M tokens", 0.0, value=current["input_usd_per_mtok"], step=0.05,
                                      format="%.2f", key="settings:input_price")
        output_price = st.number_input("Output $ / 1M tokens", 0.0, value=current["output_usd_per_mtok"], step=0.05,
                                       format="%.2f", key="settings:output_price")
        if st.form_submit_button("Save", key="settings:save", type="primary"):
            try:
                api_client.request("PUT", "/settings", json={
                    "max_rows": max_rows, "timeout_s": timeout_s, "llm_model": llm_model, "reasoning_effort": effort,
                    "input_usd_per_mtok": input_price, "output_usd_per_mtok": output_price,
                })
            except api_client.ApiError as err:
                st.error(err.detail)
            else:
                st.toast("Saved")
