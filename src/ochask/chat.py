"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import os
from datetime import date
from importlib.resources import files

from pydantic_ai import Agent, RunContext

from . import tools
from .guard import RelevanceGuard
from .search import SearchResult, search

# Re-export the shared tool context and archive configuration for integrations
# that construct or inspect the chat agent.
from .tools import (  # noqa: E402
    OCHA_TOOL_NAME,
    SOURCE_NAMES,
    OptionalArchiveToolset,
    SearchContext,
    archive_mcp_endpoint,
    archive_mcp_url,
    archive_toolset,
)

LUNA_MODEL = "gpt-6-luna"
LUNA_EFFORTS = ("low", "medium", "high")
LUNA_OUTPUT_BUDGET = 25_000


def configured_model() -> str:
    model = os.getenv("OPENAI_CHAT_MODEL", "").strip() or f"openai-responses:{LUNA_MODEL}"
    if model in {LUNA_MODEL, f"openai:{LUNA_MODEL}", f"openai-chat:{LUNA_MODEL}"}:
        return f"openai-responses:{LUNA_MODEL}"
    return model


def chat_model_config(model: str) -> dict:
    name = model.rsplit(":", 1)[-1]
    luna = model == f"openai-responses:{LUNA_MODEL}"
    return {
        "id": name,
        "label": "GPT-6 Luna" if luna else name,
        "reasoningEfforts": list(LUNA_EFFORTS) if luna else [],
        "defaultReasoningEffort": "low" if luna else None,
    }


def load_instructions(name: str) -> str:
    return files("ochask").joinpath("prompts", name).read_text(encoding="utf-8").strip()


BASE_INSTRUCTIONS = "\n\n".join([
    load_instructions("instructions.md"),
    load_instructions("mapping.md"),
])
SOURCE_INSTRUCTIONS = {
    "archive": "\n\n".join([
        load_instructions("archive.md"),
        load_instructions("bearing_witness.md"),
    ]),
    "ocha": load_instructions("ocha.md"),
}


agent = Agent(
    configured_model(),
    defer_model_check=True,
    deps_type=SearchContext,
)
relevance_guard = RelevanceGuard(
    os.getenv("OCHASK_GUARD_MODEL", "").strip() or configured_model()
)


@agent.instructions
def source_instructions(ctx: RunContext[SearchContext]) -> str:
    enabled = ctx.deps.enabled_sources if ctx.deps else SOURCE_NAMES
    return "\n\n".join([
        BASE_INSTRUCTIONS,
        *(instructions for source, instructions in SOURCE_INSTRUCTIONS.items() if source in enabled),
    ])


def search_reports(query: str, *, limit: int = 30, date_from: date | None = None, date_to: date | None = None, issue: int | None = None, report_title: str | None = None) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    return tools.search_reports(
        query,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        issue=issue,
        report_title=report_title,
        search_fn=search,
    )


tools.register_tools(
    agent,
    report_searcher=search_reports,
    archive_toolset_getter=lambda: archive_toolset,
)
