"""Local streaming chat and private PDF viewer endpoints."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from pydantic_ai.messages import ModelMessage
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ochask.chat import SearchContext, agent
from ochask.config import Settings
from ochask.storage import get_pdf

STATIC_DIR = Path(__file__).with_name("static")
CITATION = re.compile(r"\[\[(S\d+):p(\d+)\|([^\]\n]{1,500})\]\]")
# Local development only; histories are in memory and scoped by browser-generated UUID.
histories: dict[str, list[ModelMessage]] = {}
contexts: dict[str, SearchContext] = {}
locks: dict[str, asyncio.Lock] = {}


def validated_citations(answer: str, context: SearchContext) -> list[dict]:
    """Accept only sources and pages actually returned by search during this turn."""
    citations = []
    for match in CITATION.finditer(answer):
        source_id, page_text, quote = match.groups()
        source = context.sources.get(source_id)
        page = int(page_text)
        if source is None or page not in source["pages"]:
            continue
        citations.append(
            {
                "marker": match.group(),
                "report_id": source["report_id"],
                "page": page,
                "quote": quote,
                "title": source["title"],
                "landing_url": source["landing_url"],
                "source_text": source["content"],
            }
        )
    return citations


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def valid_origin(request: Request) -> bool:
    """Only accept browser requests from the same local origin."""
    origin = request.headers.get("origin")
    return not origin or origin == str(request.base_url).rstrip("/")


async def index(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "index.html")


async def pdf(request: Request) -> Response:
    try:
        report_id = UUID(request.path_params["report_id"])
        content = await run_in_threadpool(
            get_pdf, report_id, Settings.from_environment()
        )
    except ValueError, FileNotFoundError:
        return JSONResponse({"error": "PDF not found"}, status_code=404)
    except Exception:
        return JSONResponse({"error": "Could not load PDF"}, status_code=502)
    return Response(
        content,
        media_type="application/pdf",
        headers={"Cache-Control": "private, no-store"},
    )


async def chat_stream(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return JSONResponse({"error": "JSON required"}, status_code=415)
    try:
        body = await request.json()
        session = str(UUID(body.get("session", "")))
        question = body.get("question", "").strip()
        if not question:
            raise ValueError
    except ValueError, TypeError, AttributeError:
        return JSONResponse(
            {"error": "Valid session and question required"}, status_code=400
        )

    async def stream() -> AsyncIterator[str]:
        async with locks.setdefault(session, asyncio.Lock()):
            context = contexts.setdefault(session, SearchContext())
            try:
                yield event("status", {"message": "Searching reports…"})
                async with agent.run_stream(
                    question, message_history=histories.get(session, []), deps=context
                ) as result:
                    parts: list[str] = []
                    async for text in result.stream_text(delta=True):
                        parts.append(text)
                        yield event("delta", {"text": text})
                    answer = "".join(parts)
                    histories[session] = result.all_messages()
                    yield event(
                        "final",
                        {
                            "answer": answer,
                            "citations": validated_citations(answer, context),
                        },
                    )
            except Exception:
                yield event("error", {"message": "Unable to answer. Please try again."})

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


async def reset(request: Request) -> Response:
    if not valid_origin(request):
        return JSONResponse({"error": "Invalid origin"}, status_code=403)
    try:
        session = str(UUID((await request.json()).get("session", "")))
    except ValueError, TypeError, AttributeError:
        return JSONResponse({"error": "Invalid session"}, status_code=400)
    async with locks.setdefault(session, asyncio.Lock()):
        histories.pop(session, None)
        contexts.pop(session, None)
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/chat/stream", chat_stream, methods=["POST"]),
        Route("/api/chat/reset", reset, methods=["POST"]),
        Route("/api/reports/{report_id}/pdf", pdf),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ],
    middleware=[
        Middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
        )
    ],
)
