from datetime import datetime

from ui import markup

EVIL = "<img src=x onerror=alert(1)>"


def test_latest_runs_keep_the_newest_run_per_step():
    runs = [{"id": 3, "step": "profile"}, {"id": 2, "step": "parse"}, {"id": 1, "step": "parse"}]
    assert markup.latest_runs(runs) == {"profile": runs[0], "parse": runs[1]}


def test_builders_escape_backend_strings():
    built = [
        markup.item(EVIL, "error", EVIL, EVIL),
        markup.tag(EVIL, "ok"),
        markup.ident(EVIL),
        markup.steps_table([{"step": "parse", "status": "error", "started_at": None, "duration_s": None, "error": EVIL,
                         "has_result": False}]),
        markup.relationship({"from_table": EVIL, "from_columns": [EVIL], "to_table": EVIL, "to_columns": [EVIL],
                         "source": "manual", "status": "accepted", "confidence": 1.0, "cardinality": None, "note": EVIL},
                        {"action": "status", "detail": EVIL, "actor": EVIL, "at": "2026-10-21T10:41:02.123000Z"}),
        markup.match_check({"from_table": EVIL, "from_columns": [EVIL], "to_table": EVIL, "to_columns": [EVIL]},
                       {"coverage": 0.5, "from_types": [EVIL], "to_types": [EVIL], "warnings": [EVIL]}),
    ]
    for html in built:
        assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html, html


def test_clock_shows_local_time():
    assert markup.clock(None) == "—"
    ts = "2026-10-21T10:41:02.123000Z"
    assert markup.clock(ts) == datetime.fromisoformat(ts).astimezone().strftime("%H:%M")


def test_counts_summarise_model_and_relationships(client, chinook, shop):
    def counts(db):
        return markup.counts(client.get(f"/api/databases/{db}/model").json(),
                         client.get(f"/api/databases/{db}/relationships").json())

    assert counts(chinook) == [("Tables", "11", ""), ("Columns", "64", ""), ("Declared FKs", "11", ""),
                               ("Inferred edges", "0", "/ 0 to review")]
    assert counts(shop) == [("Tables", "2", ""), ("Columns", "6", ""), ("Declared FKs", "0", ""),
                            ("Inferred edges", "2", "/ 2 to review")]
    assert markup.counts(None, [])[0] == ("Tables", "—", "")
