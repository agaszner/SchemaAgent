import streamlit as st

from ui.views.management_tabs import databases, masking, relationships
from ui.widgets import current_database

TABS = {"Relationships": relationships.render, "Databases": databases.render, "Masking": masking.render}

db = current_database()
st.title("Management")
st.caption(f"{db['name']} · every change is logged (who, when, what)" if db else "No database selected")
for name, tab in zip(TABS, st.tabs(list(TABS), key="tab", bind="query-params", on_change="rerun")):
    if tab.open:
        with tab:
            TABS[name](db)
