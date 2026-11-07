import pytest


def test_settings_default_then_update(client):
    assert client.get("/api/settings").json() == {"max_rows": 100, "timeout_s": 5.0}
    assert client.put("/api/settings", json={"max_rows": 20, "timeout_s": 1.5}).json() == {"max_rows": 20, "timeout_s": 1.5}
    assert client.get("/api/settings").json() == {"max_rows": 20, "timeout_s": 1.5}


@pytest.mark.parametrize("body", [{"max_rows": 0}, {"max_rows": 1001}, {"timeout_s": 0.05},{"timeout_s": 0}, {"timeout_s": 61}])
def test_settings_reject_out_of_range(client, body):
    assert client.put("/api/settings", json=body).status_code == 422


def test_invalid_stored_settings_fall_back_to_defaults(client):
    import sqlite3
    from contextlib import closing

    from api.config import settings

    with closing(sqlite3.connect(settings.app_db)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (id, value) VALUES (1, ?)", ('{"max_rows": 0, "timeout_s": 999}',))
    assert client.get("/api/settings").json() == {"max_rows": 100, "timeout_s": 5.0}
    assert client.put("/api/settings", json={"max_rows": 7, "timeout_s": 1}).status_code == 200
    assert client.get("/api/settings").json() == {"max_rows": 7, "timeout_s": 1.0}
