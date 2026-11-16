import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai:gpt-4.1-mini")
    return importlib.import_module("ochask.chat")


def test_search_tool_requests_up_to_30_chunks(chat, monkeypatch):
    calls = []

    def fake_search(query, *, limit):
        calls.append((query, limit))
        return []

    monkeypatch.setattr(chat, "search", fake_search)
    assert chat.search_reports("water") == []
    assert calls == [("water", 30)]


def test_chat_ui_endpoint(chat):
    from ochask.web.app import app

    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/").status_code == 200
