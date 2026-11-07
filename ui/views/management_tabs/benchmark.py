"""Management · Benchmark: run the A / B / C evaluation and read the summary."""

import httpx
import pandas as pd
import streamlit as st

from ui import api_client, markup

ACTIVE = ("queued", "running")
CONFIGS = ("A", "B", "C")
RATES = {"execution_accuracy": "Execution accuracy", "valid_sql_rate": "Valid SQL rate", "repair_success": "Repair success",
         "schema_fact_accuracy": "Schema fact accuracy", "path_accuracy": "Path accuracy",
         "unsupported_claim_rate": "Unsupported claim rate", "abstention_accuracy": "Abstention accuracy"}
LABELS = {"n": "Questions", **RATES, "mean_latency_s": "Mean latency", "input_tokens": "Input tokens",
          "output_tokens": "Output tokens", "cost_usd": "Cost"}


def _format(key: str, value) -> str:
    if value is None:
        return "—"
    if key in RATES:
        return f"{value:.0%}"
    if key == "mean_latency_s":
        return f"{value:.1f} s"
    if key == "cost_usd":
        return f"${value:,.4f}"
    return f"{value:,}"


def _summary_table(summary: dict) -> pd.DataFrame:
    return pd.DataFrame({config: [_format(key, summary[config][key]) for key in LABELS] for config in CONFIGS},
                        index=list(LABELS.values()))


def render(db: dict | None) -> None:
    if db is None:
        st.info("Select a database in the sidebar.")
        return
    base = f"/databases/{db['id']}"
    runs = api_client.request("GET", f"{base}/runs")
    active = any(r["status"] in ACTIVE for r in runs)  # any step: disables the button
    bench_active = any(r["step"] == "benchmark" and r["status"] in ACTIVE for r in runs)
    if st.button("Run benchmark (A / B / C)", key="bench:run", type="primary", disabled=active):
        try:
            api_client.request("POST", f"{base}/benchmark")
        except api_client.ApiError as err:
            st.error(err.detail)
        else:
            st.rerun()

    @st.fragment(run_every="2s" if bench_active else None)
    def result() -> None:
        try:
            out = api_client.request("GET", f"{base}/benchmark")
        except httpx.TransportError:  # the backend went away mid-run
            st.error(api_client.unreachable())
            return
        if out is None:
            st.info("No benchmark run yet. Run the pipeline including Docs first.")
            return
        run = out["run"]
        if bench_active and run["status"] not in ACTIVE:
            st.rerun()  # finished: show the table and enable the button
        if run["status"] in ACTIVE:
            st.progress(run["done"] / run["total"] if run["total"] else 0.0,
                        text=f"{run['done']:,} / {run['total']:,} questions")
            return
        if run["status"] == "error":
            st.error(run["error"])
        if out["summary"]:
            st.dataframe(_summary_table(out["summary"]), width="stretch")
            with st.expander("Per question"):
                st.dataframe(pd.DataFrame(out["rows"]), width="stretch", hide_index=True)
            st.caption(f"Results: benchmark/results.csv · finished {markup.clock(run['finished_at'])}")

    result()
