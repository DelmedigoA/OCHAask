"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.resources import files

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext

from .search import SearchResult, search

load_dotenv()
model = os.getenv("OPENAI_CHAT_MODEL", "").strip()
if not model:
    raise ValueError("OPENAI_CHAT_MODEL is required")


@dataclass
class SearchContext:
    """Retrieved sources for one turn, used to validate citation references."""

    sources: dict[str, SearchResult] = field(default_factory=dict)


agent = Agent(
    model,
    deps_type=SearchContext,
    instructions=files("ochask")
    .joinpath("prompts", "instructions.md")
    .read_text(encoding="utf-8")
    .strip(),
)


def search_reports(query: str, *, limit: int = 30) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return search(query, limit=limit)


@agent.tool
def search_report_evidence(ctx: RunContext[SearchContext], query: str) -> list[dict]:
    """Search report chunks and return citation IDs, text and source pages."""
    results = search_reports(query)
    labelled = []
    for result in results:
        source_id = f"S{len(ctx.deps.sources) + 1}"
        ctx.deps.sources[source_id] = result
        labelled.append({"source_id": source_id, **result})
    return labelled
