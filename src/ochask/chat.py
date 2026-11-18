"""Pydantic AI agent for searching indexed OCHA reports."""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from .search import SearchResult, search

load_dotenv()
logger = logging.getLogger(__name__)
LUNA_MODEL = "gpt-6-luna"
LUNA_EFFORTS = ("low", "medium", "high")
LUNA_OUTPUT_BUDGET = 25_000
SOURCE_NAMES = frozenset({"archive", "ocha"})
OCHA_TOOL_NAME = "search_report_evidence"


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
        "defaultReasoningEffort": "medium" if luna else None,
    }


def archive_mcp_endpoint() -> str:
    """Return the configured Bearing Witness archive MCP endpoint."""
    return (
        os.getenv("OCHASK_ARCHIVE_MCP_URL", "https://archiveai.bw-team.com/mcp").strip()
        or "https://archiveai.bw-team.com/mcp"
    )


archive_mcp_url = archive_mcp_endpoint()


@dataclass
class OptionalArchiveToolset(WrapperToolset["SearchContext"]):
    """Keep archive MCP outages from taking down OCHAsk report chat."""

    _available: ContextVar[bool] = field(
        default_factory=lambda: ContextVar("archive_available", default=False)
    )
    _entered: ContextVar[bool] = field(
        default_factory=lambda: ContextVar("archive_entered", default=False)
    )

    async def __aenter__(self):
        try:
            await self.wrapped.__aenter__()
        except Exception as exc:
            self._available.set(False)
            self._entered.set(False)
            logger.warning(
                "Bearing Witness archive MCP unavailable: %s", type(exc).__name__
            )
        else:
            self._available.set(True)
            self._entered.set(True)
        return self

    async def __aexit__(self, *args):
        try:
            if self._entered.get():
                return await self.wrapped.__aexit__(*args)
            return None
        finally:
            self._available.set(False)
            self._entered.set(False)

    async def get_tools(
        self, ctx: RunContext["SearchContext"]
    ) -> dict[str, ToolsetTool["SearchContext"]]:
        if not self._available.get():
            return {}
        try:
            return await self.wrapped.get_tools(ctx)
        except Exception as exc:
            self._available.set(False)
            logger.warning(
                "Bearing Witness archive MCP tool discovery failed: %s",
                type(exc).__name__,
            )
            return {}


archive_toolset = OptionalArchiveToolset(
    MCPToolset(
        archive_mcp_url,
        include_instructions=False,
        tool_error_behavior="failed",
    )
)


@dataclass
class SearchContext:
    """Retrieved sources for one turn, used to validate citation references."""

    sources: dict[str, SearchResult] = field(default_factory=dict)
    enabled_sources: frozenset[str] = SOURCE_NAMES


def load_instructions(name: str) -> str:
    return files("ochask").joinpath("prompts", name).read_text(encoding="utf-8").strip()


BASE_INSTRUCTIONS = load_instructions("instructions.md")
SOURCE_INSTRUCTIONS = {
    "archive": load_instructions("archive.md"),
    "ocha": load_instructions("ocha.md"),
}


agent = Agent(
    configured_model(),
    defer_model_check=True,
    deps_type=SearchContext,
)


@agent.instructions
def source_instructions(ctx: RunContext[SearchContext]) -> str:
    enabled = ctx.deps.enabled_sources if ctx.deps else SOURCE_NAMES
    return "\n\n".join([
        BASE_INSTRUCTIONS,
        *(instructions for source, instructions in SOURCE_INSTRUCTIONS.items() if source in enabled),
    ])


@agent.toolset(per_run_step=False)
def archive_for_run(ctx: RunContext[SearchContext]) -> OptionalArchiveToolset | None:
    if ctx.deps is None or "archive" in ctx.deps.enabled_sources:
        return archive_toolset
    return None


async def prepare_ocha(
    ctx: RunContext[SearchContext], definition: ToolDefinition,
) -> ToolDefinition | None:
    if ctx.deps is None or "ocha" in ctx.deps.enabled_sources:
        return definition
    return None


def search_reports(query: str, *, limit: int = 30, date_from: date | None = None, date_to: date | None = None, issue: int | None = None, report_title: str | None = None) -> list[SearchResult]:
    """Search indexed OCHA report chunks for evidence relevant to a question."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if date_from is None and date_to is None and issue is None and report_title is None:
        return search(query, limit=limit)
    return search(query, limit=limit, date_from=date_from, date_to=date_to, issue=issue, report_title=report_title)


@agent.tool(name=OCHA_TOOL_NAME, prepare=prepare_ocha)
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
