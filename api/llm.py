"""OpenAI client. Callers use `llm.client()` so tests can patch it."""

from functools import cache

from openai import OpenAI

from api.config import settings


@cache
def _langfuse() -> None:
    """Start Langfuse once if both keys are set. Importing langfuse.openai patches the OpenAI SDK, so every call is traced."""
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        from langfuse import Langfuse
        import langfuse.openai  # noqa: F401

        Langfuse(public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key.get_secret_value(),
                 base_url=settings.langfuse_base_url)


def client() -> OpenAI:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env")
    _langfuse()
    return OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=60, max_retries=2)
