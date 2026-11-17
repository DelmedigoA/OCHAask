import importlib
from uuid import uuid4

import pytest
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.responses import JSONResponse
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


@pytest.mark.parametrize("effort", [None, "high"])
def test_chat_endpoint_uses_model_defaults(chat, monkeypatch, effort):
    from ochask.web.app import app

    calls = []

    async def fake_dispatch(request, **kwargs):
        calls.append(kwargs)
        return JSONResponse({"ok": True})

    monkeypatch.setattr(AGUIAdapter, "dispatch_request", fake_dispatch)
    headers = {"X-OCHAsk-Reasoning-Effort": effort} if effort else {}
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/chat",
            json={"threadId": str(uuid4())},
            headers=headers,
        )

    assert response.status_code == 200
    assert "model_settings" not in calls[0]
