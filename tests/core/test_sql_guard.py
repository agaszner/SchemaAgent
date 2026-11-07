import copy
import sqlite3
from contextlib import closing

import pytest

from core.parser import parse_database
from core.profiler import MASK
from core.sql_guard import GuardError, check_sql, run_sql
from core.sqlite import connect_readonly

MODEL = parse_database("data/chinook.db")
DB = "data/chinook.db"
COUNT_FOREVER = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"


@pytest.mark.parametrize("sql", ["DELETE FROM Track", "UPDATE Track SET Name = 'x'", "INSERT INTO Genre VALUES (99, 'x')",
                                 "DROP TABLE Track", "CREATE TABLE t (a)", "PRAGMA table_info(Track)",
                                 "ATTACH 'x.db' AS x", "VALUES (1)"])
def test_refuses_everything_but_select(sql):
    with pytest.raises(GuardError, match="only SELECT queries are allowed") as err:
        check_sql(sql, MODEL)
    assert err.value.kind == "invalid"


def test_refuses_empty_multiple_and_unparsable():
    for sql, message in [("", "empty query"), ("  -- only a comment", "empty query"),
                         ("SELECT 1; SELECT 2", "one statement at a time"), ("SELEC Name FROM Track", "Invalid expression")]:
        with pytest.raises(GuardError, match=message):
            check_sql(sql, MODEL)


@pytest.mark.parametrize("sql", ["SELECT * FROM Nope", "SELECT * FROM sqlite_master", "SELECT * FROM pragma_table_info('Track')",
                                 "SELECT Name FROM Track WHERE TrackId IN (SELECT 1 FROM sqlite_schema)"])
def test_refuses_tables_outside_the_model(sql):
    with pytest.raises(GuardError, match="unknown table"):
        check_sql(sql, MODEL)


def test_refuses_unknown_column():
    with pytest.raises(GuardError, match="could not be resolved") as err:
        check_sql("SELECT Nope FROM Track", MODEL)
    assert err.value.kind == "invalid"


def test_accepts_any_case_and_recursive_ctes():
    assert check_sql("select name from track", MODEL)["sql"] == "SELECT name FROM track LIMIT 100"
    check_sql("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r", MODEL)


@pytest.mark.parametrize("sql, rendered, capped", [
    ("SELECT Name FROM Track", "SELECT Name FROM Track LIMIT 100", True),
    ("SELECT Name FROM Track LIMIT 10", "SELECT Name FROM Track LIMIT 10", False),
    ("SELECT Name FROM Track LIMIT 500", "SELECT Name FROM Track LIMIT 100", True),
    ("SELECT Name FROM Track LIMIT -1", "SELECT Name FROM Track LIMIT 100", True),
    ("SELECT Name FROM Track LIMIT (SELECT 2)", "SELECT Name FROM Track LIMIT 100", True),
    ("SELECT Name FROM Track LIMIT 5, 10", "SELECT Name FROM Track LIMIT 10 OFFSET 5", False),
    ("SELECT Name FROM Artist UNION SELECT Title FROM Album", "SELECT Name FROM Artist UNION SELECT Title FROM Album LIMIT 100", True),
])
def test_limit_rules(sql, rendered, capped):
    out = check_sql(sql, MODEL)
    assert (out["sql"], out["capped"]) == (rendered, capped)


def test_masks_plain_projections_by_position():
    assert check_sql("SELECT CustomerId, Email AS e, email FROM Customer", MODEL)["masked"] == [1, 2]
    assert check_sql("SELECT Email, Email FROM Customer", MODEL)["masked"] == [0, 1]
    star = check_sql("SELECT * FROM Customer", MODEL)
    assert star["width"] == 13 and star["masked"] == [4, 9, 10, 11]  # Address, Phone, Fax, Email
    assert check_sql("SELECT Email FROM Customer WHERE CustomerId = 1 ORDER BY CustomerId", MODEL)["masked"] == [0]


def test_user_marks_apply_in_any_case():
    marks = [("Customer", "City")]
    assert check_sql("select city from customer", MODEL, marks)["masked"] == [0]
    with pytest.raises(GuardError, match=r"Customer\.City is masked"):
        check_sql("SELECT count(*) FROM Customer WHERE City = 'Paris'", MODEL, marks)


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM Customer WHERE Email LIKE 'a%'",
    "SELECT DISTINCT Email FROM Customer",
    "SELECT CustomerId FROM Customer ORDER BY Email",
    "SELECT Email FROM Customer ORDER BY 1",
    "SELECT Email AS e FROM Customer ORDER BY e",
    "SELECT Email AS FirstName FROM Customer ORDER BY FirstName",
    "SELECT Email AS e FROM Customer WHERE e LIKE 'a%'",
    "SELECT count(*) FROM Customer GROUP BY Email",
    "SELECT upper(Email) FROM Customer",
    "SELECT count(DISTINCT Email) FROM Customer",
    "SELECT Email, row_number() OVER (ORDER BY Email) FROM Customer",
    "SELECT c.CustomerId FROM Customer c JOIN Employee e ON c.Email = e.Email",
    "SELECT CustomerId FROM Customer JOIN Employee USING (Email)",
    "SELECT (SELECT count(*) FROM Customer c2 WHERE c2.Email = c.Email) FROM Customer c",
    "SELECT CustomerId FROM Customer c WHERE EXISTS (SELECT 1 WHERE c.Email LIKE 'a%')",
    "WITH x AS (SELECT Email FROM Customer) SELECT count(*) FROM x",
    "SELECT * FROM (SELECT Email FROM Customer)",
    "SELECT Email FROM Customer INTERSECT SELECT 'luisg@embraer.com.br'",
    "SELECT Email FROM Customer UNION SELECT Email FROM Employee",
    "SELECT Email AS e, count(*) FROM Customer GROUP BY CustomerId HAVING e LIKE 'a%'",
    "SELECT Email, count(*) FROM Customer GROUP BY 1",
    "SELECT CustomerId FROM Customer ORDER BY CustomerId LIMIT 1 OFFSET (SELECT count(*) FROM Customer WHERE Email < 'm')",
    *(f"SELECT CustomerId, Email FROM Customer ORDER BY {term}"
      for term in ["(2)", "2 COLLATE NOCASE", "((2) COLLATE NOCASE)", "-(-2)", "- -2", "+(2)", "(+2)"]),
])
def test_refuses_masked_columns_outside_plain_projections(sql):
    with pytest.raises(GuardError, match=r"(Customer|Employee)\.Email is masked") as err:
        check_sql(sql, MODEL)
    assert err.value.kind == "invalid"


def test_refuses_distinct_masked_but_allows_other_distinct():
    with pytest.raises(GuardError, match=r"Customer\.Email is masked"):
        check_sql("SELECT DISTINCT Email FROM Customer WHERE CustomerId IN (1, 2)", MODEL)
    assert check_sql("SELECT DISTINCT Country FROM Customer", MODEL)["masked"] == []


def test_nested_cte_does_not_whitelist_outer_table():
    sql = "SELECT sqlite_master.name FROM sqlite_master WHERE EXISTS (WITH sqlite_master AS (SELECT 1) SELECT 1 FROM sqlite_master)"
    with pytest.raises(GuardError, match="unknown table"):
        check_sql(sql, MODEL)
    assert check_sql("WITH x AS (SELECT Name FROM Track) SELECT count(*) FROM x WHERE x.Name IN (SELECT Name FROM x)", MODEL)


def test_nested_cte_does_not_expose_a_view(tmp_path):
    import sqlite3
    path = tmp_path / "v.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE People (id INTEGER, Email TEXT)")
        con.execute("CREATE VIEW v_people AS SELECT * FROM People")
    sql = "SELECT v_people.Email FROM v_people WHERE EXISTS (WITH v_people AS (SELECT 1) SELECT 1 FROM v_people)"
    with pytest.raises(GuardError, match="unknown table"):
        check_sql(sql, parse_database(path))


def test_refuses_in_table_form():
    with pytest.raises(GuardError, match="IN Genre is not allowed") as err:
        check_sql("SELECT 1 FROM (SELECT 1 AS Genre) WHERE 1 IN Genre", MODEL)
    assert err.value.kind == "invalid"


def test_deeply_nested_input_is_invalid():
    with pytest.raises(GuardError) as err:
        check_sql("SELECT " + "(" * 3000 + "1" + ")" * 3000, MODEL)
    assert err.value.kind == "invalid"


def _view_db(tmp_path):
    import sqlite3
    path = tmp_path / "v.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE People (id INTEGER, Email TEXT)")
        con.execute("CREATE VIEW v_people AS SELECT * FROM People")
    return parse_database(path)


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM (SELECT 1) AS sqlite_master, sqlite_master",
    "SELECT * FROM (SELECT 1 AS a) AS sqlite_master, sqlite_master",
    "SELECT * FROM (SELECT 1 AS a) AS x, sqlite_master AS x",
    "WITH x AS (SELECT 1) SELECT * FROM x, sqlite_master AS x",
    "SELECT Name FROM Artist UNION SELECT name FROM sqlite_master",
])
def test_derived_alias_or_cte_does_not_whitelist_real_table(sql):
    with pytest.raises(GuardError, match="unknown table"):
        check_sql(sql, MODEL)


def test_derived_alias_does_not_expose_a_view(tmp_path):
    with pytest.raises(GuardError, match="unknown table"):
        check_sql("SELECT * FROM (SELECT 1 AS a) AS v_people, v_people", _view_db(tmp_path))


@pytest.mark.parametrize("sql", [
    "WITH x AS (SELECT Name FROM Track) SELECT Name FROM X",
    "WITH x AS (SELECT Name FROM Track), y AS (SELECT Name FROM x) SELECT count(*) FROM y",
    "WITH x AS (SELECT Name FROM Track) SELECT (SELECT count(*) FROM x) FROM x AS y",
    "WITH x AS (SELECT TrackId FROM Track) SELECT 1 WHERE 1 IN (SELECT TrackId FROM x)",
    "WITH x AS (SELECT Name FROM Track) SELECT Name FROM x UNION SELECT Name FROM x",
])
def test_accepts_cte_references(sql):
    check_sql(sql, MODEL)


def _db(tmp_path, *statements):
    import sqlite3
    path = tmp_path / "u.db"
    with sqlite3.connect(path) as con:
        for statement in statements:
            con.execute(statement)
    return parse_database(path)


@pytest.mark.parametrize("sql", [
    'WITH "v_uſers" AS (SELECT 1 AS a) SELECT * FROM v_users',
    "WITH v_uſers AS (SELECT 1 AS a) SELECT * FROM v_users",
])
def test_unicode_cte_name_does_not_shadow_view(tmp_path, sql):
    model = _db(tmp_path, "CREATE TABLE People (id INTEGER, Email TEXT)", "CREATE VIEW v_users AS SELECT * FROM People")
    with pytest.raises(GuardError, match="unknown table"):
        check_sql(sql, model)


def test_kelvin_cte_name_does_not_shadow_view(tmp_path):
    model = _db(tmp_path, "CREATE TABLE People (id INTEGER, Email TEXT)", "CREATE VIEW v_kunden AS SELECT * FROM People")
    with pytest.raises(GuardError, match="unknown table"):
        check_sql('WITH "v_Kunden" AS (SELECT 1 AS a) SELECT * FROM v_kunden', model)


def test_unicode_cte_name_does_not_shadow_sqlite_master():
    with pytest.raises(GuardError, match="unknown table"):
        check_sql('WITH "ſqlite_master" AS (SELECT 1 AS a) SELECT * FROM sqlite_master', MODEL)


def test_ascii_cte_name_in_any_case_is_a_cte():
    check_sql("WITH SQLITE_MASTER AS (SELECT 1 AS a) SELECT * FROM sqlite_master", MODEL)


def test_unicode_table_name_is_not_folded_onto_model_table(tmp_path):
    model = _db(tmp_path, "CREATE TABLE Strasse (id INTEGER, Email TEXT)", 'CREATE VIEW "Straße" AS SELECT * FROM Strasse')
    with pytest.raises(GuardError, match="unknown table"):
        check_sql('SELECT * FROM "Straße"', model)


def test_non_ascii_masked_column_is_masked(tmp_path):
    model = _db(tmp_path, 'CREATE TABLE Kunde (id INTEGER, "Größe" TEXT)')
    marks = [("Kunde", "Größe")]
    assert check_sql('SELECT "Größe" FROM Kunde', model, marks)["masked"] == [0]
    with pytest.raises(GuardError, match="is masked"):
        check_sql("SELECT id FROM Kunde WHERE \"Größe\" = 'x'", model, marks)


def test_runs_join_and_reports_the_cap():
    out = run_sql(DB, "SELECT ar.Name, count(*) AS albums FROM Artist ar JOIN Album al ON al.ArtistId = ar.ArtistId "
                      "GROUP BY ar.ArtistId ORDER BY albums DESC, ar.Name", MODEL, max_rows=3)
    assert out["columns"] == ["Name", "albums"] and out["rows"] == [["Iron Maiden", 21], ["Led Zeppelin", 14], ["Deep Purple", 11]]
    assert out["limited"] and out["sql"].endswith("LIMIT 3")


def test_masks_values_by_position_and_user_mark():
    out = run_sql(DB, "SELECT CustomerId, Email AS e FROM Customer ORDER BY CustomerId", MODEL)
    assert out["rows"][0] == [1, MASK] and out["masked"] == ["e"] and len(out["rows"]) == 59 and not out["limited"]
    assert run_sql(DB, "SELECT City FROM Customer", MODEL, [("Customer", "City")])["rows"][0] == [MASK]
    assert run_sql(DB, 'SELECT Name AS "Email" FROM Artist', MODEL)["masked"] == ["Email"]  # name patterns apply to output names too


def test_keeps_duplicate_column_names():
    out = run_sql(DB, "SELECT * FROM Album JOIN Artist ON Album.ArtistId = Artist.ArtistId", MODEL, max_rows=1)
    assert out["columns"] == ["AlbumId", "Title", "ArtistId", "ArtistId", "Name"] and len(out["rows"][0]) == 5


def test_timeout():
    with pytest.raises(GuardError, match=r"longer than 0\.2 s") as err:
        run_sql(DB, COUNT_FOREVER, MODEL, timeout_s=0.2)
    assert err.value.kind == "timeout"


def test_sqlite_errors_are_execution_errors():
    with pytest.raises(GuardError, match="no such function") as err:
        run_sql(DB, "SELECT nosuchfunc(Name) FROM Track", MODEL)
    assert err.value.kind == "execution"


def test_stale_model_fails_closed():
    stale = copy.deepcopy(MODEL)
    next(t for t in stale["tables"] if t["name"] == "Customer")["columns"].pop()  # SupportRepId
    with pytest.raises(GuardError, match="run parse again") as err:
        run_sql(DB, "SELECT * FROM Customer", stale)
    assert err.value.kind == "execution"


def test_rendered_sql_keeps_sqlite_semantics():
    sql = ("SELECT strftime('%Y', InvoiceDate), BillingCity || ' x', BillingCity GLOB 'S*', IFNULL(BillingState, '?'), "
           "substr(BillingCity, 1, 3), iif(Total > 5, 'big', 'small'), 7 / 2, group_concat(BillingCountry, ', ') OVER () "
           "FROM Invoice ORDER BY InvoiceId LIMIT 3")
    with closing(connect_readonly(DB)) as conn:
        assert run_sql(DB, sql, MODEL)["rows"] == [list(r) for r in conn.execute(sql)]


def test_spaced_names_and_blobs(tmp_path):
    path = tmp_path / "spaced.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('CREATE TABLE "Order Details" (Id INTEGER PRIMARY KEY, "Unit Price" REAL, Data BLOB);'
                           "INSERT INTO \"Order Details\" VALUES (1, 1.5, x'010203');")
    out = run_sql(path, 'SELECT "Unit Price", Data FROM "Order Details"', parse_database(path))
    assert out["columns"] == ["Unit Price", "Data"] and out["rows"] == [[1.5, "<3 bytes>"]]


def test_invalid_utf8_neither_leaks_nor_fails(tmp_path):
    path = tmp_path / "bad.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("CREATE TABLE Customer (Id INTEGER PRIMARY KEY, Email TEXT);"
                           "INSERT INTO Customer VALUES (1, CAST(x'ff' || 'john.smith@mail.com' AS TEXT));")
    out = run_sql(path, "SELECT Id, Email FROM Customer", parse_database(path))
    assert out["rows"] == [[1, MASK]] and out["masked"] == ["Email"]


def test_user_mark_survives_a_same_width_reorder(tmp_path):
    path = tmp_path / "t.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("CREATE TABLE t (Id INTEGER, City TEXT, Note TEXT);")
    model = parse_database(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("DROP TABLE t; CREATE TABLE t (City TEXT, Id INTEGER, Note TEXT);"
                           "INSERT INTO t VALUES ('Budapest', 1, 'n');")
    out = run_sql(path, "SELECT * FROM t", model, [("t", "City")])
    assert out["rows"][0][out["columns"].index("City")] == MASK


def test_instant_failure_is_not_a_timeout():
    with pytest.raises(GuardError, match="no such function") as err:
        run_sql(DB, "SELECT nosuchfunc(Name) FROM Track", MODEL, timeout_s=0)
    assert err.value.kind == "execution"


def test_missing_database_is_an_execution_error(tmp_path):
    with pytest.raises(GuardError) as err:
        run_sql(tmp_path / "nope.db", "SELECT Name FROM Track", MODEL)
    assert err.value.kind == "execution"


@pytest.mark.parametrize("term", ["(1)", "1 COLLATE BINARY"])
def test_refuses_wrapped_group_by_ordinal_of_user_marked_column(term):
    with pytest.raises(GuardError, match=r"Customer\.City is masked"):
        check_sql(f"SELECT City, count(*) FROM Customer GROUP BY {term}", MODEL, [("Customer", "City")])


@pytest.mark.parametrize("term", ["random()", "CustomerId + 0"])
def test_column_free_or_column_order_terms_still_pass_with_masked_projection(term):
    assert check_sql(f"SELECT CustomerId, Email FROM Customer ORDER BY {term}", MODEL)["masked"] == [1]


def test_using_star_width_mismatch_gives_actionable_message():
    with pytest.raises(GuardError, match="list the columns") as err:
        run_sql(DB, "SELECT Album.*, Artist.* FROM Album JOIN Artist USING (ArtistId)", MODEL)
    assert err.value.kind == "execution"
