"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext

from .search import SearchResult, search

load_dotenv()
model = os.getenv("OPENAI_CHAT_MODEL", "").strip() or None


@dataclass
class SearchContext:
    """Retrieved sources for one turn, used to validate citation references."""

    sources: dict[str, SearchResult] = field(default_factory=dict)


agent = Agent(
    model,
    defer_model_check=True,
    deps_type=SearchContext,
    instructions=files("ochask")
    .joinpath("prompts", "instructions.md")
    .read_text(encoding="utf-8")
    .strip(),
)


def search_reports(query: str, *, limit: int = 30, date_from: date | None = None, date_to: date | None = None, issue: int | None = None, report_title: str | None = None) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if date_from is None and date_to is None and issue is None and report_title is None:
        return search(query, limit=limit)
    return search(query, limit=limit, date_from=date_from, date_to=date_to, issue=issue, report_title=report_title)


@agent.tool
def search_report_evidence(
    ctx: RunContext[SearchContext], query: str, date_from: date | None = None,
    date_to: date | None = None, issue: int | None = None, report_title: str | None = None,
) -> list[dict]:
    """Search report chunks and return citation IDs, text and source pages.

    When the user specifies a publication date range or an issue number, pass
    those filters. When a specific series or location is supplied, pass its exact title fragment in report_title.

    Args:
        query: Evidence to find, including the report series or location if given.
        date_from: Inclusive earliest publication date (YYYY-MM-DD), if specified.
        date_to: Inclusive latest publication date (YYYY-MM-DD), if specified.
        issue: Issue number, for example 300 for #300, if specified.
        report_title: Exact title fragment identifying the report series or location, if specified.
    """
    results = search_reports(query, date_from=date_from, date_to=date_to, issue=issue, report_title=report_title)
    labelled = []
    for result in results:
        source_id = f"S{len(ctx.deps.sources) + 1}"
        ctx.deps.sources[source_id] = result
        labelled.append({"source_id": source_id, **result})
    return labelled
