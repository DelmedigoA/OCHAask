"""Local assistant-ui chat API and frontend."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
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
    headers = None
    if request.url.path in {"/", "/corpus"}:
        headers = {
            "Link": '</api/corpus>; rel=preload; as=fetch; crossorigin=anonymous'
        }
    return FileResponse(target, headers=headers)


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
        import json

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

    from ochask.chat import SearchContext, agent

    from pydantic_ai.usage import UsageLimits

    from pydantic_ai.ui.ag_ui import AGUIAdapter

    return await AGUIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=SearchContext(),
        conversation_id=thread_id,
        manage_message_history="client",
        usage_limits=UsageLimits(
            request_limit=5,
            tool_calls_limit=3,
            input_tokens_limit=40_000,
            per_request_input_tokens_limit=20_000,
            output_tokens_limit=2_048,
            count_tokens_before_request=True,
        ),
        timeout=90,
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
