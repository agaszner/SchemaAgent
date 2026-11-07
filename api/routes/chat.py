"""Chat with the agent (spec §2); every turn is logged with its evidence and the user's feedback."""

import openai
from fastapi import APIRouter, HTTPException

from api import llm
from api.appdb import llm_params, load_sources, read_app_settings
from api.deps import Conn, Database
from api.payloads import ChatIn, ChatOut, FeedbackIn
from core.agent import HISTORY_TURNS, answer_question

router = APIRouter(prefix="/databases/{database_id}/chat", tags=["chat"])

COLUMNS = ("kind", "answer", "input_tokens", "output_tokens", "cost_usd", "latency_s")


@router.post("", status_code=201, response_model=ChatOut)
def ask(body: ChatIn, db: Database, conn: Conn) -> dict:
    sources = load_sources(conn, db["id"])  # raises NotReady
    app = read_app_settings(conn)
    history = [dict(r) for r in conn.execute(
        "SELECT question, answer FROM (SELECT id, question, answer FROM chat_messages WHERE database_id = ?"
        " ORDER BY id DESC LIMIT ?) ORDER BY id", (db["id"], HISTORY_TURNS))]
    marks = [(m["table"], m["column"]) for m in db["masked_columns"]]  # tuples, as in routes/sql.py
    # Only reads so far, so no write transaction stays open while the model answers
    try:
        turn = answer_question(llm.client(), body.question, sources, history=history, db_path=db["path"],
                               marks=marks, llm=llm_params(app))
    except openai.APIError as err:
        raise HTTPException(502, f"OpenAI error: {err}") from None
    # A mark saved while the model answered: its tool outputs may hold the newly masked values
    conn.execute("BEGIN IMMEDIATE")
    now = conn.execute("SELECT masked_columns FROM databases WHERE id = ?", (db["id"],)).fetchone()[0]
    if now != db["masked_columns"]:
        raise HTTPException(409, "Masking changed while the model was answering; ask again.")
    evidence = {k: v for k, v in turn.items() if k not in COLUMNS}
    return dict(conn.execute(
        f"INSERT INTO chat_messages (database_id, question, evidence, llm_model, {', '.join(COLUMNS)})"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
        (db["id"], body.question, evidence, app.llm_model, *(turn[k] for k in COLUMNS)),
    ).fetchone())


@router.get("", response_model=list[ChatOut])
def read_chat(db: Database, conn: Conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM chat_messages WHERE database_id = ? ORDER BY id", (db["id"],))]


@router.put("/{message_id}/feedback", response_model=ChatOut)
def set_feedback(message_id: int, body: FeedbackIn, db: Database, conn: Conn) -> dict:
    row = conn.execute("UPDATE chat_messages SET feedback = ? WHERE id = ? AND database_id = ? RETURNING *",
                       (body.feedback, message_id, db["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "message not found")
    return dict(row)
