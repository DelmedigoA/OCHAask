"""Local assistant-ui chat API and frontend."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ochask.chat import SearchContext, agent
from ochask.config import Settings
from ochask.corpus import corpus_overview, search_evidence
from ochask.database import count_indexed_reports
from ochask.web.workspace import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    save_conversation,
)

STATIC_DIR = Path(__file__).with_name("static")
WORKSPACE_DIR = STATIC_DIR / "workspace"
contexts: dict[str, SearchContext] = {}
CHAT_EFFORTS = frozenset({"low", "medium", "high"})


async def index(_: Request) -> Response:
    target = WORKSPACE_DIR / "index.html"
    if not target.exists():
        return JSONResponse(
            {"error": "Chat UI is not built. Run npm run build in frontend/."},
            status_code=503,
        )
    return FileResponse(target)


def valid_origin(request: Request) -> bool:
    """Only accept browser mutations from the same local origin."""
    origin = request.headers.get("origin")
    return not origin or origin == str(request.base_url).rstrip("/")


async def conversations(request: Request) -> Response:
    settings = Settings.from_environment()
    if request.method == "GET":
        return JSONResponse({"conversations": list_conversations(settings)})
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    if request.method == "POST":
        return JSONResponse({"id": create_conversation()}, status_code=201)
    try:
        payload = await request.json()
        conversation_id = request.path_params["conversation_id"]
        if request.method == "PUT":
            messages = payload.get("messages", [])
            if not isinstance(messages, list):
                raise ValueError
            save_conversation(
                settings,
                conversation_id,
                payload.get("title", "New research chat"),
                messages,
            )
            return JSONResponse({"ok": True})
        if request.method == "DELETE":
            UUID(conversation_id)
            delete_conversation(settings, conversation_id)
            contexts.pop(conversation_id, None)
            return JSONResponse({"ok": True})
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"error": "Invalid conversation"}, status_code=400)
    return JSONResponse({"error": "Not found"}, status_code=404)


async def workspace_stats(_: Request) -> Response:
    try:
        settings = Settings.from_environment()
        count = count_indexed_reports(settings.supabase_url, settings.supabase_secret_key)
    except (RuntimeError, ValueError):
        return JSONResponse({"error": "Report count is temporarily unavailable"}, status_code=503)
    return JSONResponse({"available_pdfs": count})


async def corpus(request: Request) -> Response:
    try:
        settings = Settings.from_environment()
        result = await run_in_threadpool(corpus_overview, settings)
    except (RuntimeError, ValueError):
        return JSONResponse(
            {"error": "The indexed corpus is temporarily unavailable"},
            status_code=503,
        )
    return JSONResponse(result)


async def search_reports(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    try:
        payload = await request.json()
        query = payload.get("query", "")
        limit = payload.get("limit", 10)
        if not isinstance(query, str) or not query.strip():
            raise ValueError
        if isinstance(limit, bool) or not isinstance(limit, int) or limit not in {5, 10, 20, 30}:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return JSONResponse(
            {"error": "A search query and result count of 5, 10, 20, or 30 are required"},
            status_code=400,
        )

    try:
        settings = Settings.from_environment()
        results = await run_in_threadpool(
            search_evidence, query.strip(), limit, settings
        )
    except Exception:
        return JSONResponse(
            {"error": "Search is temporarily unavailable"}, status_code=503
        )
    return JSONResponse({"results": results})


async def load_conversation(request: Request) -> Response:
    try:
        item = get_conversation(
            Settings.from_environment(), request.path_params["conversation_id"]
        )
    except ValueError:
        item = None
    return JSONResponse(
        item or {"error": "Conversation not found"},
        status_code=200 if item else 404,
    )


async def agui_chat(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    effort = request.headers.get(
        "x-ochask-reasoning-effort", "medium"
    ).strip().lower()
    if effort not in CHAT_EFFORTS:
        return JSONResponse(
            {"error": "Reasoning effort must be low, medium, or high"},
            status_code=400,
        )
    try:
        payload = await request.json()
        conversation_id = str(UUID(payload.get("threadId", "")))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"error": "Valid threadId required"}, status_code=400)

    from pydantic_ai.ui.ag_ui import AGUIAdapter

    return await AGUIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=contexts.setdefault(conversation_id, SearchContext()),
        conversation_id=conversation_id,
        manage_message_history="client",
        model_settings={"openai_reasoning_effort": effort},
    )


app = Starlette(
    routes=[
        Route("/", index),
        Route("/corpus", index),
        Route("/search", index),
        Route("/chat", index),
        Route("/api/conversations", conversations, methods=["GET", "POST"]),
        Route("/api/stats", workspace_stats),
        Route("/api/corpus", corpus, methods=["GET"]),
        Route("/api/search", search_reports, methods=["POST"]),
        Route(
            "/api/conversations/{conversation_id}",
            conversations,
            methods=["PUT", "DELETE"],
        ),
        Route("/api/conversations/{conversation_id}/load", load_conversation),
        Route("/api/chat", agui_chat, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ],
    middleware=[
        Middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
        )
    ],
)
