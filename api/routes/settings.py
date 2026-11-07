"""User settings: the row cap and timeout for SQL queries."""

from fastapi import APIRouter

from api.appdb import read_app_settings
from api.deps import Conn
from api.payloads import AppSettings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def get_settings(conn: Conn) -> AppSettings:
    return read_app_settings(conn)


@router.put("", response_model=AppSettings)
def put_settings(body: AppSettings, conn: Conn) -> AppSettings:
    conn.execute(
        "INSERT INTO app_settings (id, value) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET value = excluded.value",
        (body.model_dump(),),
    )
    return body
