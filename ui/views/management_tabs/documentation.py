"""Management · Documentation: review, edit and approve the LLM's table descriptions."""

from urllib.parse import quote

import streamlit as st

from ui import api_client

CERTAINTY = ["certain", "likely", "uncertain"]


def _save(base: str, table: str, doc: dict, status: str) -> None:
    body = {"summary": st.session_state[f"docs:summary:{table}"], "status": status, "columns": [
        {"name": c["name"], "description": st.session_state[f"docs:desc:{table}:{c['name']}"],
         "certainty": st.session_state[f"docs:cert:{table}:{c['name']}"]} for c in doc["columns"]]}
    try:
        api_client.request("PUT", f"{base}/{quote(table, safe='')}", json=body)
    except api_client.ApiError as err:
        st.error(err.detail)
        return
    st.toast("Saved" if status == "edited" else "Approved")
    st.rerun()


def render(db: dict | None) -> None:
    if db is None:
        st.info("Select a database in the sidebar.")
        return
    base = f"/databases/{db['id']}/docs"
    docs = api_client.request("GET", base)
    if not docs:
        st.info("No documentation yet. Run the Docs step on the Pipeline page.")
        return
    stale = [d["table"] for d in docs if d["stale"]]
    approved = sum(d["status"] == "approved" for d in docs)
    cost = sum(d["cost_usd"] for d in docs)
    st.caption(f"{len(docs):,} tables · {approved:,} approved · {len(stale):,} stale · ${cost:,.4f}")

    by_table = {d["table"]: d for d in docs}
    table = st.selectbox("Table", list(by_table), key="docs:table")
    doc = by_table[table]
    st.caption(f"{doc['status']}{' · stale' if doc['stale'] else ''} · {doc['llm_model']}")
    if st.session_state.get(f"docs:version:{table}") != doc["updated_at"]:  # new server text: drop the old widget values
        for key in [k for k in st.session_state if k.startswith((f"docs:summary:{table}", f"docs:desc:{table}:", f"docs:cert:{table}:"))]:
            del st.session_state[key]
        st.session_state[f"docs:version:{table}"] = doc["updated_at"]
    with st.form(f"docs:form:{table}"):
        st.text_area("Summary", doc["summary"], key=f"docs:summary:{table}")
        for column in doc["columns"]:
            name, description, certainty = st.columns([2, 5, 2], vertical_alignment="center")
            name.code(column["name"], language=None)
            description.text_input("Description", column["description"], key=f"docs:desc:{table}:{column['name']}",
                                   label_visibility="collapsed")
            certainty.selectbox("Certainty", CERTAINTY, CERTAINTY.index(column["certainty"]),
                                key=f"docs:cert:{table}:{column['name']}", label_visibility="collapsed")
        save, approve, _ = st.columns([1, 1, 4])
        if save.form_submit_button("Save", key="docs:save"):
            _save(base, table, doc, "edited")
        if approve.form_submit_button("Approve", key="docs:approve", type="primary"):
            _save(base, table, doc, "approved")

    if st.button("Regenerate stale tables", key="docs:regenerate", disabled=not stale):
        try:
            api_client.request("POST", f"{base}/regenerate", json={"tables": stale})
        except api_client.ApiError as err:
            st.error(err.detail)
        else:
            st.toast(f"Regenerating {len(stale):,} tables: progress on the Pipeline page")
            st.rerun()
    st.warning("Regenerating replaces edited and approved text.")
    if st.button("Export Markdown", key="docs:export"):
        try:
            written = api_client.request("POST", f"{base}/export")["written"]
        except api_client.ApiError as err:
            st.error(err.detail)
        else:
            st.success(f"Wrote {len(written):,} files.")
