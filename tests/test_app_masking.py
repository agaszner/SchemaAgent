PATTERN = ["Customer.Address", "Customer.Phone", "Customer.Fax", "Customer.Email", "Employee.Address",
           "Employee.Phone", "Employee.Fax", "Employee.Email", "Invoice.BillingAddress"]


def test_masking_marks_a_column_and_requires_reprofile(ui, client, chinook):
    def marks():
        return client.get(f"/api/databases/{chinook}").json()["masked_columns"]

    at = ui("views/management.py", database_id=chinook, tab="Masking")
    assert [t.key for t in at.toggle if t.disabled] == [f"mask:{c}" for c in PATTERN]
    at.button(key="mask_save").click().run()  # nothing changed: no PUT, so the profile stays
    assert at.info[0].value == "No changes to save."
    assert client.get(f"/api/databases/{chinook}/profile").status_code == 200
    at.multiselect(key="mask_add").select("Genre.Name")
    at.button(key="mask_save").click().run()
    assert marks() == [{"table": "Genre", "column": "Name"}]
    assert [t.key for t in at.toggle] == ["mask:Genre.Name"]  # the profile was cleared
    steps = next(h.value for h in ui("views/pipeline.py", database_id=chinook).html if "ss-steps" in h.value)
    assert ">cleared<" in steps  # the ledger no longer claims the profile is done
    at.button(key="mask_run_profile").click().run()
    assert [t.key for t in at.toggle if t.disabled] == [f"mask:{c}" for c in PATTERN]
    at.toggle(key="mask:Genre.Name").set_value(False)
    at.button(key="mask_save").click().run()
    assert marks() == []
