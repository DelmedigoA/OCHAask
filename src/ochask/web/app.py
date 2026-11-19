"""Local assistant-ui chat API and frontend."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ochask.config import Settings
from ochask.corpus import corpus_overview, search_evidence
from ochask.database import count_indexed_reports

STATIC_DIR = Path(__file__).with_name("static")
WORKSPACE_DIR = STATIC_DIR / "workspace"
MAX_CHAT_BODY_BYTES = 1024 * 1024
MAX_CHAT_INPUT_CHARS = 6_000
MAX_SEARCH_QUERY_CHARS = 4_000


def log_request(scope, status: int, started: float, stream_completed: bool) -> None:
    print(
        json.dumps(
            {
                "endpoint": scope.get("path", ""),
                "status": status,
                "latency_ms": round((perf_counter() - started) * 1000),
                "stream_completed": stream_completed,
            }
        ),
        flush=True,
    )


class AccessLogMiddleware:
    """Log request outcome and stream completion without request contents."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500
        stream_completed = False

        async def logged_send(message):
            nonlocal status, stream_completed
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                stream_completed = True
            await send(message)

        try:
            await self.app(scope, receive, logged_send)
        except Exception:
            log_request(scope, status, started, stream_completed)
            raise
        log_request(scope, status, started, stream_completed)


async def index(request: Request) -> Response:
    target = WORKSPACE_DIR / "index.html"
    if not target.exists():
        return JSONResponse(
            {"error": "Chat UI is not built. Run npm run build in frontend/."},
            status_code=503,
        )
    content = target.read_bytes()
    headers = {
        "Cache-Control": "no-store",
        "ETag": f'"{hashlib.sha256(content).hexdigest()}"',
    }
    if request.url.path in {"/", "/corpus"}:
        headers["Link"] = (
            '</api/corpus>; rel=preload; as=fetch; crossorigin=anonymous'
        )
    return Response(content, media_type="text/html", headers=headers)


def valid_origin(request: Request) -> bool:
    """Require browser mutations to match the deployed request origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    host = request.headers.get("host", "").lower()
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if not parsed.scheme or parsed.netloc.lower() != host:
        return False
    if parsed.scheme == forwarded_proto:
        return True
    return parsed.scheme == "https" and forwarded_proto.split(",", 1)[0].strip() == "https"


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
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=3600"
        },
    )


async def search_reports(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    try:
        payload = await request.json()
        query = payload.get("query", "")
        limit = payload.get("limit", 10)
        if not isinstance(query, str) or not query.strip():
            raise ValueError
        if len(query) > MAX_SEARCH_QUERY_CHARS:
            return JSONResponse({"error": "Search query is too large"}, status_code=413)
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


async def agui_chat(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    try:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_CHAT_BODY_BYTES:
            return JSONResponse({"error": "Chat request is too large"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_CHAT_BODY_BYTES:
            return JSONResponse({"error": "Chat request is too large"}, status_code=413)
        payload = json.loads(body)
        thread_id = str(UUID(payload.get("threadId", "")))
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError
        latest_user = next(
            (message for message in reversed(messages)
             if isinstance(message, dict) and message.get("role") == "user"),
            None,
        )
        content = latest_user.get("content", []) if latest_user else []
        if isinstance(content, str):
            input_chars = len(content)
        elif isinstance(content, list):
            input_chars = sum(
                len(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text", ""), str)
            )
        else:
            input_chars = 0
        if input_chars > MAX_CHAT_INPUT_CHARS:
            return JSONResponse(
                {"error": f"Chat input must be {MAX_CHAT_INPUT_CHARS:,} characters or less"},
                status_code=413,
            )
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"error": "Valid threadId required"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Invalid chat request"}, status_code=400)

    from ag_ui.core import (
        RunFinishedEvent,
        RunStartedEvent,
        StateSnapshotEvent,
        TextMessageContentEvent,
        TextMessageEndEvent,
        TextMessageStartEvent,
        UserMessage,
    )
    from pydantic import ValidationError
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings
    from pydantic_ai.usage import UsageLimits
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    from ochask.chat import (
        LUNA_OUTPUT_BUDGET,
        SearchContext,
        agent,
        chat_model_config,
        configured_model,
        relevance_guard,
    )
    from ochask.guard import GUARDRAIL_MESSAGE, GUARD_UNAVAILABLE_MESSAGE
    from ochask.web.chat_options import ChatOptions, filter_history, run_tool_sources

    model = configured_model()
    config = chat_model_config(model)
    try:
        forwarded = payload.get("forwardedProps")
        options = ChatOptions.model_validate(forwarded.get("ochask", {}) if isinstance(forwarded, dict) else {})
    except ValidationError:
        return JSONResponse({"error": "Invalid chat settings"}, status_code=400)
    if not options.enabled_sources:
        return JSONResponse({"error": "Enable Archive or OCHA before sending"}, status_code=400)
    if options.modelId is not None and options.modelId != config["id"]:
        return JSONResponse({"error": "This chat model is unavailable"}, status_code=400)
    if options.reasoningEffort is not None and options.reasoningEffort not in config["reasoningEfforts"]:
        return JSONResponse({"error": "This model does not support the selected reasoning effort"}, status_code=400)

    try:
        adapter = await AGUIAdapter.from_request(request, agent=agent)
    except ValidationError:
        return JSONResponse({"error": "Invalid AG-UI request"}, status_code=422)
    history, history_sources = filter_history(adapter.run_input.messages, options)
    adapter.run_input = adapter.run_input.model_copy(update={
        "messages": history,
        "tools": None,
        "context": None,
        "state": None,
    })

    current_user = next(
        (message for message in reversed(history) if isinstance(message, UserMessage)), None,
    )

    def message_text(message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
                for part in content
                if isinstance(
                    part.get("text", "") if isinstance(part, dict) else getattr(part, "text", ""),
                    str,
                )
            )
        return ""

    if current_user is None:
        return JSONResponse({"error": "A user message is required"}, status_code=400)

    conversational_history = [
        {"role": message.role, "text": message_text(message)[:1500]}
        for message in history
        if message.role in {"user", "assistant"} and message_text(message).strip()
    ][-5:]
    allowed, guard_unavailable = await relevance_guard.check(
        message_text(current_user),
        conversational_history[:-1],
        options.enabled_sources,
    )
    if not allowed:
        guardrail_text = (
            GUARD_UNAVAILABLE_MESSAGE if guard_unavailable else GUARDRAIL_MESSAGE
        )
        guard_message_id = str(uuid4())
        run_id = str(adapter.run_input.run_id)

        async def guardrail_events():
            yield RunStartedEvent(threadId=thread_id, runId=run_id)
            yield TextMessageStartEvent(messageId=guard_message_id, role="assistant")
            yield TextMessageContentEvent(messageId=guard_message_id, delta=guardrail_text)
            yield TextMessageEndEvent(messageId=guard_message_id)
            yield StateSnapshotEvent(snapshot={
                "ochaskGuardrailMessageId": None if guard_unavailable else guard_message_id,
                "ochaskSourceContext": {
                    "userMessageId": current_user.id,
                    "sources": [],
                },
            })
            yield RunFinishedEvent(threadId=thread_id, runId=run_id)

        return adapter.streaming_response(guardrail_events())

    async def record_sources(result):
        if current_user is not None:
            dependencies = history_sources | run_tool_sources(result.new_messages())
            yield StateSnapshotEvent(snapshot={
                "ochaskSourceContext": {
                    "userMessageId": current_user.id,
                    "sources": sorted(dependencies),
                },
            })

    model_settings = None
    output_budget = 2_048
    if effort := options.reasoningEffort or config["defaultReasoningEffort"]:
        output_budget = LUNA_OUTPUT_BUDGET
        model_settings = OpenAIResponsesModelSettings(
            openai_reasoning_effort=effort,
            openai_store=False,
            max_tokens=output_budget,
        )

    return adapter.streaming_response(adapter.run_stream(
        deps=SearchContext(enabled_sources=options.enabled_sources),
        model=model,
        model_settings=model_settings,
        conversation_id=thread_id,
        usage_limits=UsageLimits(
            request_limit=5,
            tool_calls_limit=15,
            input_tokens_limit=128_000,
            per_request_input_tokens_limit=128_000,
            output_tokens_limit=output_budget,
            count_tokens_before_request=True,
        ),
        timeout=90,
        on_complete=record_sources,
    ))


async def chat_config(_: Request) -> Response:
    from ochask.chat import chat_model_config, configured_model

    return JSONResponse(
        {"model": chat_model_config(configured_model())},
        headers={"Cache-Control": "no-store"},
    )


def allowed_hosts() -> list[str]:
    """Return Vercel's assigned hostnames plus explicitly configured domains."""
    hosts = {"localhost", "127.0.0.1", "[::1]"}
    for name in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        value = os.getenv(name, "").strip().lower()
        if value:
            hosts.add(value.removeprefix("https://").removeprefix("http://").split("/", 1)[0])
    hosts.update(
        value.strip().lower()
        for value in os.getenv("OCHASK_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    )
    return sorted(hosts)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/corpus", index),
        Route("/search", index),
        Route("/chat", index),
        Route("/api/stats", workspace_stats),
        Route("/api/corpus", corpus, methods=["GET"]),
        Route("/api/search", search_reports, methods=["POST"]),
        Route("/api/chat/config", chat_config, methods=["GET"]),
        Route("/api/chat", agui_chat, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ],
    middleware=[
        Middleware(AccessLogMiddleware),
        Middleware(
            TrustedHostMiddleware, allowed_hosts=allowed_hosts()
        )
    ],
)
