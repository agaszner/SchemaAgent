def _sql(client, db, sql):
    return client.post(f"/api/databases/{db}/sql", json={"sql": sql})


def test_sql_uses_stored_limit_and_masks(client, chinook):
    client.put("/api/settings", json={"max_rows": 5, "timeout_s": 5})
    body = _sql(client, chinook, "SELECT CustomerId, Email FROM Customer ORDER BY CustomerId").json()
    assert body["sql"] == "SELECT CustomerId, Email FROM Customer ORDER BY CustomerId LIMIT 5"
    assert body["rows"][0] == [1, "***"] and body["masked"] == ["Email"] and body["limited"]


def test_sql_applies_user_marks(client, chinook):
    client.put(f"/api/databases/{chinook}/masking", json={"columns": [{"table": "Customer", "column": "City"}]})
    assert _sql(client, chinook, "SELECT City FROM Customer").json()["rows"][0] == ["***"]
    refused = _sql(client, chinook, "SELECT count(*) FROM Customer WHERE City = 'Paris'")
    assert refused.status_code == 400 and "Customer.City is masked" in refused.json()["detail"]


def test_sql_refusal_and_timeout_are_400(client, chinook):
    r = _sql(client, chinook, "DELETE FROM Track")
    assert r.status_code == 400 and r.json()["detail"] == "only SELECT queries are allowed"
    client.put("/api/settings", json={"max_rows": 100, "timeout_s": 0.2})
    r = _sql(client, chinook, "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r")
    assert r.status_code == 400 and "longer than 0.2 s" in r.json()["detail"]


def test_sql_needs_a_parsed_database(client, shop_db):
    db = client.post("/api/databases", json={"path": str(shop_db)}).json()["id"]
    assert _sql(client, db, "SELECT 1").status_code == 409
    assert _sql(client, 999, "SELECT 1").status_code == 404
