"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext

from .config import Settings
from .database import indexed_report_stats
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
)


@agent.instructions
def indexed_corpus_instructions(_: RunContext[SearchContext]) -> str:
    """Render current index size and posted-date bounds into the prompt."""
    prompt = _load_prompt("instructions.md")
    try:
        settings = Settings.from_environment()
        report_count, min_date, max_date = indexed_report_stats(
            settings.supabase_url, settings.supabase_secret_key
        )
        count_text = f"{report_count:,}"
        min_date_text = min_date or "unknown"
        max_date_text = max_date or "unknown"
    except (RuntimeError, ValueError):
        count_text = "unavailable"
        min_date_text = "unknown"
        max_date_text = "unknown"

    return (
        prompt.replace("{indexed_report_count}", count_text)
        .replace("{earliest_report_date}", min_date_text)
        .replace("{latest_report_date}", max_date_text)
    )


def search_reports(query: str, *, limit: int | None = None) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    result_limit = (
        int(os.getenv("OCHASK_CHAT_SEARCH_LIMIT", "30")) if limit is None else limit
    )
    if not 1 <= result_limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return search(query, limit=result_limit)


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
