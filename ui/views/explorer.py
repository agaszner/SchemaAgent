import streamlit as st

from ui import api_client, widgets


def go_to_page(page: int) -> None:
    st.session_state["rows:page"] = page


def apply_filter() -> None:
    st.session_state["rows:page"] = 0
    value = st.session_state["rows:value"]
    st.session_state["rows:filter"] = (
        (st.session_state["rows:column"], st.session_state["rows:op"], value) if value else None)


def render_data(db: dict, table: dict) -> None:
    ss = st.session_state
    if ss.get("rows:table") != (db["id"], table["name"]):  # a new table starts clean
        for key in ("rows:sort", "rows:desc", "rows:column", "rows:op", "rows:value"):
            ss.pop(key, None)
        ss.update({"rows:table": (db["id"], table["name"]), "rows:page": 0, "rows:filter": None})
    try:
        probe = api_client.rows(db["id"], table["name"], limit=1)  # the server decides what is masked
    except api_client.ApiError as err:
        st.error(err.detail)
        return
    if probe is None:
        st.info("Run parse again: the database changed since the last parse.")
        return
    masked = set(probe["masked"])
    open_cols = [c["name"] for c in table["columns"] if c["name"] not in masked]

    reset = lambda: go_to_page(0)  # noqa: E731
    size, sort, desc = st.columns([1, 2, 1])
    limit = size.selectbox("Rows per page", [25, 50, 100], key="rows:limit", on_change=reset)
    sort_by = sort.selectbox("Sort by", ["—", *open_cols], key="rows:sort", on_change=reset)
    descending = desc.toggle("Descending", key="rows:desc", on_change=reset)
    with st.form("rows_filter"):
        col, op, value = st.columns([2, 1, 3])
        col.selectbox("Column", open_cols, key="rows:column")
        op.selectbox("Match", ["contains", "equals"], key="rows:op")
        value.text_input("Value", key="rows:value", placeholder="Value…")
        st.form_submit_button("Apply", key="rows:apply", on_click=apply_filter)

    page, applied = ss["rows:page"], ss["rows:filter"]
    params = {"offset": page * limit, "limit": limit}
    if sort_by != "—":
        params |= {"sort": sort_by, "desc": descending}
    if applied:
        params |= dict(zip(("column", "op", "value"), applied))
    try:
        result = api_client.rows(db["id"], table["name"], **params)
    except api_client.ApiError as err:
        st.error(err.detail)
        return
    if result is None:
        st.info("Run parse first: the Explorer reads the parsed schema.")
        return
    total = result["total"]
    if total == 0:
        st.caption("No rows match." if applied else "This table is empty.")
        return
    pages = -(-total // limit)
    st.dataframe(result["rows"], hide_index=True, height=(len(result["rows"]) + 1) * 35 + 3)
    start, end = page * limit + 1, page * limit + len(result["rows"])
    st.caption(f"Rows {start:,}–{end:,} of {total:,} · page {page + 1:,} of {pages:,}")
    prev, nxt, _ = st.columns([1, 1, 6])
    prev.button("Previous", key="rows:prev", disabled=page == 0, on_click=go_to_page, args=(page - 1,))
    nxt.button("Next", key="rows:next", disabled=page >= pages - 1, on_click=go_to_page, args=(page + 1,))


def open_table(name: str) -> None:
    st.session_state["explorer_filter"] = ""  # the sidebar filter would hide the target
    st.session_state["explorer_table"] = name  # a callback may set a widget's state; the script body cannot


def profile_rows(table: dict, stats: dict) -> list[dict]:
    pk = set(table["primary_key"])
    fks = {c: fk["ref_table"] for fk in table["foreign_keys"] for c in fk["columns"]}
    by_name = {c["name"]: c for c in stats["columns"]}
    rows = []
    for col in table["columns"]:
        p = by_name[col["name"]]
        if p["masked"]:
            span, top = "***", "masked"
        else:
            span = "" if p["min"] is None and p["max"] is None else f"{p['min']} – {p['max']}"
            top = ("every value distinct" if stats["row_count"] and p["distinct"] == stats["row_count"]
                   else " · ".join(f"{v['value']} {v['count']}" for v in p["top_values"]))
        rows.append({
            "Column": col["name"],
            "Key": "PK" if col["name"] in pk else f"FK → {fks[col['name']]}" if col["name"] in fks else "",
            "Type": col["type"],
            "Nulls": p["null_ratio"] * 100,
            "Distinct": p["distinct"],
            "Range": span,
            "Top values": top,
        })
    return rows


db = widgets.require_database()
st.title("Explorer")
st.caption(f"{db['name']} · read-only")

model = api_client.model(db["id"])
if model is None:
    st.info("Run parse first: the Explorer reads the parsed schema.")
    if st.button("Run parse", key="run:parse"):
        widgets.start_run(db["id"], "parse")
    st.stop()

profile = api_client.profile(db["id"])
stats = {t["table"]: t for t in profile["tables"]} if profile else {}
tables = {t["name"]: t for t in model["tables"]}

with st.sidebar:
    needle = st.text_input("Filter tables", key="explorer_filter", placeholder="Filter tables…").lower()
    names = [n for n in tables if needle in n.lower()]
    if not names:
        st.caption("No table matches.")
        st.stop()
    if st.session_state.get("explorer_table") not in names:
        st.session_state["explorer_table"] = names[0]
    st.radio("Tables", names, key="explorer_table", format_func=lambda n: (
        f"{n} · {stats[n]['row_count']:,}" if n in stats else n))

name = st.session_state["explorer_table"]
table = tables[name]
rows = stats[name]["row_count"] if name in stats else None
parts = ([f"{rows:,} rows"] if rows is not None else []) + [f"{len(table['columns'])} columns"]
st.subheader(name)
parts.append(f"primary key {', '.join(table['primary_key'])}" if table["primary_key"] else "no primary key")
st.caption(" · ".join(parts))

out = list(dict.fromkeys(fk["ref_table"] for fk in table["foreign_keys"] if fk["ref_table"] != name))
chips = [(f"→ {ref}", ref, f"open:{ref}") for ref in out]
for other in tables.values():
    if other["name"] != name and any(fk["ref_table"] == name for fk in other["foreign_keys"]):
        chips.append((f"← {other['name']}", other["name"], f"open-in:{other['name']}" if other["name"] in out else f"open:{other['name']}"))
if chips:
    for col, (label, target, key) in zip(st.columns(len(chips)), chips):
        col.button(label, key=key, on_click=open_table, args=(target,))

profile_tab, data_tab = st.tabs(["Profile", "Data"])
with profile_tab:
    if name not in stats or not {c["name"] for c in table["columns"]} <= {c["name"] for c in stats[name]["columns"]}:
        st.info("Run profile to see column statistics.")
        if st.button("Run profile", key="run:profile"):
            widgets.start_run(db["id"], "profile")
    else:
        grid = profile_rows(table, stats[name])
        st.dataframe(grid, hide_index=True, height=(len(grid) + 1) * 35 + 3, column_config={
            "Column": st.column_config.TextColumn(width="medium"),
            "Nulls": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%")})
with data_tab:
    render_data(db, table)
