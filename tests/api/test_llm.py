import pytest

from api import llm
from api.config import settings


def test_client_needs_a_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.client()
