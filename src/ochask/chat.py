"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext

from .search import SearchResult, search

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    """Load a prompt from the repository's prompts directory."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


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
    system_prompt=_load_prompt("system.md"),
    instructions=_load_prompt("instructions.md"),
)


def search_reports(query: str) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    return search(query, limit=30)


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
