import sqlite3
from contextlib import closing

import httpx
import openai

from api import llm
from api.config import settings

COUNT = [("run_sql", {"sql": "SELECT COUNT(*) FROM Customer"})]


def ask(client, db, question="How many customers?"):
    return client.post(f"/api/databases/{db}/chat", json={"question": question})


def test_chat_answers_and_logs(client, chinook, fake_llm):
    fake_llm.script = [COUNT, {"kind": "data", "answer": "59.", "tables": ["Customer"], "columns": []}]
    r = ask(client, chinook)
    assert r.status_code == 201 and r.json()["kind"] == "data"
    out = r.json()
    assert out["evidence"]["result"]["rows"] == [[59]]
    assert out["evidence"]["sql"].startswith("SELECT COUNT(*) FROM Customer")  # the guard appends a LIMIT
    assert not {"kind", "answer", "input_tokens", "output_tokens", "cost_usd", "latency_s"} & set(out["evidence"])
    assert (out["llm_model"], out["input_tokens"], out["output_tokens"], out["feedback"]) == ("gpt-6-luna", 200, 40, None)
    assert client.get(f"/api/databases/{chinook}/chat").json() == [out]


def test_history_is_sent(client, chinook, fake_llm):
    fake_llm.script = [COUNT, {"kind": "data", "answer": "59.", "tables": [], "columns": []},
                       COUNT, {"kind": "data", "answer": "Still 59.", "tables": [], "columns": []}]
    ask(client, chinook)
    before = len(fake_llm.calls)
    ask(client, chinook, "And now?")
    assert fake_llm.calls[before]["input"][:3] == [
        {"role": "user", "content": "How many customers?"}, {"role": "assistant", "content": "59."},
        {"role": "user", "content": "And now?"}]
    assert [m["question"] for m in client.get(f"/api/databases/{chinook}/chat").json()] == [
        "How many customers?", "And now?"]


def test_feedback_round_trip(client, chinook, fake_llm, tmp_path):
    fake_llm.reply = {"kind": "abstain", "answer": "Not stored.", "tables": [], "columns": []}
    msg = ask(client, chinook).json()["id"]
    url = f"/api/databases/{chinook}/chat/{msg}/feedback"
    assert client.put(url, json={"feedback": "up"}).json()["feedback"] == "up"
    assert client.get(f"/api/databases/{chinook}/chat").json()[0]["feedback"] == "up"
    assert client.put(url, json={"feedback": None}).json()["feedback"] is None
    assert client.put(url, json={"feedback": "meh"}).status_code == 422
    (tmp_path / "other.db").write_bytes((tmp_path / "chinook.db").read_bytes())
    other = client.post("/api/databases", json={"path": str(tmp_path / "other.db")}).json()["id"]
    assert client.put(f"/api/databases/{other}/chat/{msg}/feedback", json={"feedback": "up"}).status_code == 404


def test_chat_before_parse_is_409(client, tmp_path):
    db = client.post("/api/databases", json={"path": str(tmp_path / "chinook.db")}).json()["id"]
    r = ask(client, db)
    assert r.status_code == 409 and r.json()["detail"] == "run parse first"


def test_chat_without_key_is_503(client, chinook, monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: (_ for _ in ()).throw(RuntimeError("OPENAI_API_KEY is not set: add it to .env")))
    r = client.post(f"/api/databases/{chinook}/chat", json={"question": "x"})
    assert r.status_code == 503 and ".env" in r.json()["detail"]


def test_masking_change_clears_the_chat(client, chinook, fake_llm):
    fake_llm.reply = {"kind": "abstain", "answer": "Not stored.", "tables": [], "columns": []}
    ask(client, chinook)
    mark = [{"table": "Customer", "column": "City"}]
    assert client.put(f"/api/databases/{chinook}/masking", json={"columns": mark}).status_code == 200
    assert client.get(f"/api/databases/{chinook}/chat").json() == []


def test_removing_a_mark_keeps_the_chat(client, chinook, fake_llm):
    url = f"/api/databases/{chinook}/masking"
    client.put(url, json={"columns": [{"table": "Customer", "column": "City"}]})
    client.post(f"/api/databases/{chinook}/runs", json={"step": "profile"})  # the mark cleared the cached profile
    fake_llm.reply = {"kind": "abstain", "answer": "Not stored.", "tables": [], "columns": []}
    assert ask(client, chinook).status_code == 201
    assert client.put(url, json={"columns": []}).status_code == 200
    assert client.put(url, json={"columns": []}).status_code == 200  # a no-op save
    assert len(client.get(f"/api/databases/{chinook}/chat").json()) == 1


def test_masking_change_during_the_answer_is_409(client, chinook, fake_llm, monkeypatch):
    class MaskingFake(type(fake_llm)):
        def parse(self, **kwargs):  # a mark saved while the model answers
            with closing(sqlite3.connect(settings.app_db, timeout=0)) as conn, conn:
                conn.execute("UPDATE databases SET masked_columns = ? WHERE id = ?",
                             ('[{"table": "Customer", "column": "City"}]', chinook))
            return super().parse(**kwargs)

    masking = MaskingFake()
    masking.reply = {"kind": "abstain", "answer": "Not stored.", "tables": [], "columns": []}
    monkeypatch.setattr(llm, "client", lambda: masking)
    r = ask(client, chinook)
    assert r.status_code == 409 and r.json()["detail"].startswith("Masking changed")
    assert client.get(f"/api/databases/{chinook}/chat").json() == []


def test_openai_error_is_502(client, chinook, fake_llm, monkeypatch):
    def fail(**kwargs):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))

    monkeypatch.setattr(fake_llm, "parse", fail)
    r = ask(client, chinook)
    assert r.status_code == 502 and r.json()["detail"] == "OpenAI error: Connection error."


def test_empty_question_is_422(client, chinook):
    assert ask(client, chinook, "").status_code == 422
    assert ask(client, chinook, "x" * 2001).status_code == 422


def test_holds_no_lock_during_the_llm_call(client, chinook, fake_llm, monkeypatch):
    class EditingFake(type(fake_llm)):
        def parse(self, **kwargs):  # a relationship edit while the model answers must not hit "database is locked"
            with closing(sqlite3.connect(settings.app_db, timeout=0)) as conn, conn:
                conn.execute("UPDATE relationships SET note = 'edited' WHERE database_id = ?", (chinook,))
            return super().parse(**kwargs)

    editing = EditingFake()
    editing.reply = {"kind": "abstain", "answer": "Not stored.", "tables": [], "columns": []}
    monkeypatch.setattr(llm, "client", lambda: editing)
    ask(client, chinook)  # a prior turn, so the history read runs too
    assert ask(client, chinook, "Again?").status_code == 201
