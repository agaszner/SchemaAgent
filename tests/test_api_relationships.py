def test_review_filters_accepts_rejects_and_logs(client, shop):
    url = f"/api/databases/{shop}/relationships"
    strong, weak = client.get(url, params={"source": "inferred"}).json()
    assert [(r["to_table"], r["confidence"], r["status"]) for r in (strong, weak)] == [
        ("Customer", 1.0, "proposed"), ("Orders", 0.8, "proposed")]
    accepted = client.post(f"{url}/accept", json={"min_confidence": 0.9}).json()
    assert [r["id"] for r in accepted] == [strong["id"]]
    r = client.patch(f"/api/relationships/{weak['id']}", json={"status": "rejected"}, headers={"X-Actor": "anna"})
    assert r.json()["status"] == "rejected"
    r = client.patch(f"/api/relationships/{weak['id']}", json={"status": "accepted"}, headers={"X-Actor": ""})
    assert r.status_code == 422  # an empty actor; the status and log checks below show it changed nothing
    assert client.get(url, params={"status": "accepted"}).json() == [{**strong, "status": "accepted"}]
    log = client.get(f"{url}/log").json()
    assert [(e["relationship_id"], e["detail"], e["actor"]) for e in log] == [
        (weak["id"], "proposed -> rejected", "anna"),
        (strong["id"], "proposed -> accepted (bulk >= 0.9)", "local")]


def test_rerun_keeps_decisions(client, shop):
    url = f"/api/databases/{shop}/relationships"
    weak = next(r for r in client.get(url).json() if r["to_table"] == "Orders")
    client.patch(f"/api/relationships/{weak['id']}", json={"status": "rejected"})
    assert client.post(f"/api/databases/{shop}/runs", json={"step": "all"}).status_code == 202
    after = client.get(url).json()
    assert len(after) == 2
    assert next(r for r in after if r["id"] == weak["id"])["status"] == "rejected"


def test_delete_disables_inferred_and_refuses_declared(client, chinook, shop):
    declared = client.get(f"/api/databases/{chinook}/relationships").json()[0]
    assert client.delete(f"/api/relationships/{declared['id']}").status_code == 409
    inferred = client.get(f"/api/databases/{shop}/relationships").json()[0]
    assert client.delete(f"/api/relationships/{inferred['id']}").status_code == 204
    rejected = client.get(f"/api/databases/{shop}/relationships", params={"status": "rejected"}).json()
    assert [r["id"] for r in rejected] == [inferred["id"]]
    assert client.delete("/api/relationships/999").status_code == 404


def test_path_follows_review_decisions(client, chinook):
    url = f"/api/databases/{chinook}/path"
    path = client.get(url, params={"source": "Customer", "target": "Track"}).json()
    assert [(s["from_table"], s["to_table"]) for s in path] == [
        ("Invoice", "Customer"), ("InvoiceLine", "Invoice"), ("InvoiceLine", "Track")]
    rels = client.get(f"/api/databases/{chinook}/relationships").json()
    invoice_customer = next(r for r in rels if (r["from_table"], r["to_table"]) == ("Invoice", "Customer"))
    client.patch(f"/api/relationships/{invoice_customer['id']}", json={"status": "rejected"})
    assert client.get(url, params={"source": "Customer", "target": "Track"}).json() is None
    assert client.get(url, params={"source": "Nope", "target": "Track"}).status_code == 404


def test_path_needs_a_graph_run(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    client.post(f"/api/databases/{db}/runs", json={"step": "parse"})
    r = client.get(f"/api/databases/{db}/path", params={"source": "Customer", "target": "Track"})
    assert r.status_code == 409 and r.json()["detail"] == "run graph first"


GOOD = {"from_table": "Invoice", "from_columns": ["CustomerId"], "to_table": "Customer", "to_columns": ["CustomerId"]}
LOW = {"from_table": "Track", "from_columns": ["GenreId"], "to_table": "MediaType", "to_columns": ["MediaTypeId"]}


def test_check_reports_coverage_types_and_warnings(client, chinook):
    url = f"/api/databases/{chinook}/relationships/check"
    assert client.post(url, json=GOOD).json() == {
        "coverage": 1.0, "from_types": ["INT"], "to_types": ["INT"], "warnings": []}
    low = client.post(url, json=LOW).json()
    assert low["coverage"] == 0.2
    assert low["warnings"] == ["only 20% of Track.GenreId values exist in MediaType.MediaTypeId"]
    typed = client.post(url, json={"from_table": "Customer", "from_columns": ["Email"],
                                   "to_table": "Employee", "to_columns": ["EmployeeId"]}).json()
    assert "type mismatch: Customer.Email VARCHAR(60) vs Employee.EmployeeId INT" in typed["warnings"]
    for bad in [{**GOOD, "from_table": "Nope"}, {**GOOD, "to_columns": ["Nope"]},
                {**GOOD, "to_columns": []}, {**GOOD, "from_columns": ["CustomerId", "InvoiceId"]}]:
        assert client.post(url, json=bad).status_code == 422, bad


def test_manual_relationship_is_created_logged_and_deleted(client, chinook, shop):
    url = f"/api/databases/{chinook}/relationships"
    r = client.post(url, json={**LOW, "cardinality": "1:N", "note": "demo"}, headers={"X-Actor": "anna"})
    assert r.status_code == 201
    rel = r.json()
    assert (rel["source"], rel["status"], rel["confidence"], rel["cardinality"]) == ("manual", "accepted", 1.0, "1:N")
    assert client.post(url, json=LOW).status_code == 409
    assert client.delete(f"/api/relationships/{rel['id']}").status_code == 204
    assert rel["id"] not in [r["id"] for r in client.get(url).json()]
    assert [(e["action"], e["actor"]) for e in client.get(f"{url}/log").json()] == [
        ("delete", "local"), ("create", "anna")]

    inferred = {"from_table": "Orders", "from_columns": ["CustomerId"], "to_table": "Customer", "to_columns": ["CustomerId"]}
    r = client.post(f"/api/databases/{shop}/relationships", json=inferred)
    assert r.status_code == 409 and "proposed" in r.json()["detail"]
