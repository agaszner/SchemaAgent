"""HTTP client for the SchemaSage backend. Every call goes through request(), which adds /api."""

import os
from typing import Any

import httpx

http = httpx.Client(base_url=os.environ.get("SCHEMASAGE_API_URL", "http://127.0.0.1:8000"), timeout=60)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError, TypeError):
        return response.text
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(e["msg"] for e in detail)
    return str(detail)


def unreachable() -> str:
    return f"Cannot reach the SchemaSage API at {http.base_url}. Start it with `uv run fastapi dev`."


def request(method: str, path: str, **kwargs) -> Any:
    """JSON body of the response, None for 204. Raises ApiError for 4xx/5xx; transport errors propagate."""
    response = http.request(method, "/api" + path, **kwargs)
    if response.status_code >= 400:
        raise ApiError(response.status_code, _detail(response))
    return None if response.status_code == 204 else response.json()


def _result(database_id: int, kind: str) -> dict | None:
    try:
        return request("GET", f"/databases/{database_id}/{kind}")
    except ApiError as err:
        if err.status == 409:  # the step has not run yet
            return None
        raise


def model(database_id: int) -> dict | None:
    return _result(database_id, "model")


def profile(database_id: int) -> dict | None:
    return _result(database_id, "profile")
