"""OpenAI client. Callers use `llm.client()` so tests can patch it."""

from openai import OpenAI

from api.config import settings


def client() -> OpenAI:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env")
    return OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=60, max_retries=2)
