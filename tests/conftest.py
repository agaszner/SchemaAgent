import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app.db; tmp_path is the data dir and holds a copy of chinook.db."""
    shutil.copy("data/chinook.db", tmp_path / "chinook.db")
    monkeypatch.setattr(settings, "app_db", tmp_path / "app.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as c:  # runs the lifespan, so init_db creates the schema
        yield c


@pytest.fixture
def chinook(client, tmp_path) -> int:
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})  # TestClient returns after background tasks finish
    return db


@pytest.fixture
def shop_db(tmp_path) -> Path:
    """Two inferred candidates: Orders.CustomerId -> Customer.CustomerId (1.0) and -> Orders.OrderId (0.79)."""
    path = tmp_path / "shop.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Orders (OrderId INTEGER PRIMARY KEY, CustomerId INTEGER, Qty INTEGER);
            INSERT INTO Customer VALUES (1, 'a'), (2, 'b'), (3, 'c');
            INSERT INTO Orders VALUES (1, 1, 1), (2, 3, 1), (3, 3, 1), (4, 2, 1);
        """)
    return path


@pytest.fixture
def shop(client, shop_db) -> int:
    db = client.post("/api/databases", json={"path": str(shop_db)}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "all"})
    return db


from streamlit.testing.v1 import AppTest

from app import api


@pytest.fixture
def ui(client, monkeypatch):
    """Runs the Streamlit app against the in-process backend.

    ui() runs app/main.py with its default page (Home). ui("views/x.py") runs main.py and then that page,
    as in production, so main.py replaces a database_id that is not registered with the first registered one.
    """
    monkeypatch.setattr(api, "http", client)

    def run(page: str | None = None, database_id: int | None = None, tab: str | None = None) -> AppTest:
        at = AppTest.from_file("../app/main.py", default_timeout=30)  # relative to this file
        if database_id is not None:
            at.session_state["database_id"] = database_id
        if tab is not None:
            at.query_params["tab"] = tab
        return (at.switch_page(page) if page else at).run()

    return run
