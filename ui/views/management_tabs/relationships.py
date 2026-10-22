"""Management · Relationships: review list (filters, decisions, bulk accept, change log)."""

import streamlit as st

from ui import api_client, markup

LABELS = {"accept": "Accept", "reject": "Reject", "disable": "Disable", "delete": "Delete"}
DISABLE_WARNING = ("Declared foreign keys come from the schema. Disabling hides this edge from the graph, "
                   "JOIN paths and the agent. You can accept it again later.")


def actions(source: str, status: str) -> list[str]:
    if source == "manual":
        return ["delete"]
    if status == "proposed":
        return ["accept", "reject"]
    if status == "rejected":
        return ["accept"]
    return ["disable"] if source == "declared" else ["delete"]


def visible(rels: list[dict], source: str, status: str, min_confidence: float) -> list[dict]:
    return [r for r in rels if source in ("all", r["source"]) and status in ("all", r["status"])
            and r["confidence"] >= min_confidence]


def bulk_count(rels: list[dict], threshold: float) -> int:
    """Rows POST /accept changes: proposed ones at or above the threshold."""
    return len(visible(rels, "all", "proposed", threshold))


def _call(method: str, path: str, **kwargs) -> None:
    """Send a decision; an error shows beside the clicked button."""
    try:
        api_client.request(method, path, **kwargs)
    except api_client.ApiError as err:
        st.error(err.detail)  # a failed confirming PATCH keeps its notice open
        return
    st.session_state.pop("confirm_disable", None)
    st.rerun()


def _buttons(rel: dict) -> None:
    rel_id = rel["id"]
    if st.session_state.get("confirm_disable") == rel_id:
        if st.button("Disable", key=f"confirm_disable:{rel_id}", type="primary"):
            _call("PATCH", f"/relationships/{rel_id}", json={"status": "rejected"})
        if st.button("Cancel", key="cancel_disable"):
            st.session_state.pop("confirm_disable", None)
            st.rerun()
        return
    for action in actions(rel["source"], rel["status"]):
        primary = action == "accept" and rel["status"] == "proposed"
        if not st.button(LABELS[action], key=f"{action}:{rel_id}", type="primary" if primary else "secondary"):
            continue
        if action == "disable":
            st.session_state["confirm_disable"] = rel_id
            st.rerun()
        elif action == "delete":
            _call("DELETE", f"/relationships/{rel_id}")
        else:
            _call("PATCH", f"/relationships/{rel_id}", json={"status": "accepted" if action == "accept" else "rejected"})


def _review(db: dict) -> None:
    base = f"/databases/{db['id']}/relationships"
    rels = api_client.request("GET", base)
    log = api_client.request("GET", base + "/log")  # newest first
    st.subheader("Relationships")
    st.caption(f"{len(rels)} · {sum(r['status'] == 'proposed' for r in rels)} suggested")
    source = st.segmented_control("Source", ["all", "declared", "inferred", "manual"], default="all",
                                  required=True, key="rel_source")
    status = st.segmented_control("Status", ["all", "proposed", "accepted", "rejected"],
                                  format_func=lambda s: markup.STATUS_LABEL.get(s, (s,))[0], default="all",
                                  required=True, key="rel_status")
    # a browser drag can send 0.29000000000000004
    threshold = round(st.slider("Confidence ≥", 0.0, 1.0, 0.0, 0.01, format="%.2f", key="rel_confidence"), 2)
    count = bulk_count(rels, threshold)
    if st.button(f"Accept all ≥ {threshold:.2f} ({count})", key="bulk_accept", disabled=count == 0):
        _call("POST", base + "/accept", json={"min_confidence": threshold})

    last: dict[int, dict] = {}
    for entry in log:
        last.setdefault(entry["relationship_id"], entry)
    for rel in visible(rels, source, status, threshold):
        text, buttons = st.columns([5, 2])
        with text:
            markup.show(markup.relationship(rel, last.get(rel["id"])))
            if st.session_state.get("confirm_disable") == rel["id"]:
                markup.show(markup.notice("warn", "declared FK", DISABLE_WARNING))
        with buttons:
            _buttons(rel)
    st.caption("Rejected pairs are not suggested again after a re-run. Declared FKs can only be disabled.")

    names = {r["id"]: f"{markup.ref(r['from_table'], r['from_columns'])} → {markup.ref(r['to_table'], r['to_columns'])}"
             for r in rels}
    with st.expander("Change log"):
        st.dataframe([{"When": markup.clock(e["at"], "%Y-%m-%d %H:%M"), "Who": e["actor"],
                       "Relationship": names.get(e["relationship_id"], f"#{e['relationship_id']}"),
                       "Action": e["action"], "Detail": e["detail"]} for e in log], hide_index=True)


FORM_KEYS = ("nr_from_table", "nr_from_columns", "nr_to_table", "nr_to_columns", "nr_cardinality", "nr_note")


def _reset_form() -> None:
    for key in (*FORM_KEYS, "nr_error"):
        st.session_state.pop(key, None)


def _body() -> dict:
    state = st.session_state
    return {"from_table": state.get("nr_from_table"), "from_columns": state.get("nr_from_columns", []),
            "to_table": state.get("nr_to_table"), "to_columns": state.get("nr_to_columns", []),
            "cardinality": state.get("nr_cardinality"), "note": state.get("nr_note") or None}


def _save(database_id: int) -> None:
    """on_click: reads the form from session state, because widget edits made with the click land there first."""
    try:
        api_client.request("POST", f"/databases/{database_id}/relationships", json=_body())
    except api_client.ApiError as err:
        st.session_state["nr_error"] = err.detail
        return
    _reset_form()
    st.toast("Saved")


def _side(tables: dict[str, list[str]], side: str) -> list[str]:
    table_key, columns_key = f"nr_{side}_table", f"nr_{side}_columns"
    table = st.selectbox("Source table" if side == "from" else "Target table", list(tables), index=None,
                         key=table_key, on_change=lambda: st.session_state.update({columns_key: []}))
    return st.multiselect("Columns", tables.get(table, []), key=columns_key)


def _new_relationship(db: dict) -> None:
    model = api_client.model(db["id"])
    if model is None:
        st.info("Run parse first: the form lists the parsed tables.")
        return
    tables = {t["name"]: [c["name"] for c in t["columns"]] for t in model["tables"]}
    st.subheader("New relationship")
    from_columns = _side(tables, "from")
    to_columns = _side(tables, "to")
    st.segmented_control("Cardinality (optional)", ["1:1", "1:N", "N:M"], key="nr_cardinality")
    st.text_input("Note", key="nr_note", placeholder="Why do these columns belong together?")
    if from_columns and to_columns:
        # ponytail: the check re-runs on every rerun while the form is filled; cache it if big tables make the tab slow
        body = _body()
        try:
            markup.show(markup.match_check(body, api_client.request("POST", f"/databases/{db['id']}/relationships/check", json=body)))
        except api_client.ApiError as err:
            st.error(err.detail)
    if "nr_error" in st.session_state:
        st.error(st.session_state["nr_error"])
    st.button("Save", key="nr_save", type="primary", disabled=not (from_columns and to_columns),
              on_click=_save, args=(db["id"],))
    st.button("Cancel", key="nr_cancel", on_click=_reset_form)


def render(db: dict | None) -> None:
    if db is None:
        st.info("Select a database in the sidebar.")
        return
    left, right = st.columns([3, 2])
    with left:
        _review(db)
    with right, st.container(border=True):
        _new_relationship(db)
