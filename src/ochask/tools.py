"""Tool definitions and toolsets for the Bearing Witness Research Assistant."""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from dotenv import load_dotenv
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from .bearing_witness_document import read_document_pages, search_document
from .geocoding import mapping
from .project_resources import bearing_witness_faqs, project_metadata
from .search import SearchResult, search

load_dotenv()

logger = logging.getLogger(__name__)
SOURCE_NAMES = frozenset({"archive", "ocha"})
OCHA_TOOL_NAME = "search_ocha_corpus"
ARCHIVE_SEARCH_TOOLS = frozenset({"search_archive", "lookup_url"})
OCHA_PROVENANCE_FIELDS = ("platform", "author", "url", "page_url")


def _is_ocha_provenance(item: object) -> bool:
    """Return True when an archive item is an OCHA document or archived copy."""
    if not isinstance(item, dict):
        return False
    haystack = " ".join(
        str(item.get(field, "")) for field in OCHA_PROVENANCE_FIELDS
    ).lower()
    return "ocha" in haystack or "ochaopt.org" in haystack


def _filter_ocha_payload(payload):
    """Remove OCHA-provenance items from an archive search result.

    The archive indexes copies of OCHA documents. When the OCHA source is
    disabled those copies must not contribute evidence, so they are dropped
    before the model sees the result.
    """
    if isinstance(payload, list):
        return [item for item in payload if not _is_ocha_provenance(item)]
    if not isinstance(payload, dict):
        return payload
    if _is_ocha_provenance(payload):
        return {"error": "This archived item belongs to the disabled OCHA source."}
    cataloged = payload.get("cataloged")
    if isinstance(cataloged, dict) and isinstance(cataloged.get("hits"), list):
        hits = cataloged["hits"]
        filtered = [item for item in hits if not _is_ocha_provenance(item)]
        return {**payload, "cataloged": {**cataloged, "hits": filtered}}
    hits = payload.get("hits")
    if isinstance(hits, list):
        filtered = [item for item in hits if not _is_ocha_provenance(item)]
        return {**payload, "hits": filtered}
    return payload


def archive_mcp_endpoint() -> str:
    """Return the configured Bearing Witness archive MCP endpoint."""
    return (
        os.getenv("OCHASK_ARCHIVE_MCP_URL", "https://archiveai.bw-team.com/mcp").strip()
        or "https://archiveai.bw-team.com/mcp"
    )


archive_mcp_url = archive_mcp_endpoint()


@dataclass
class SearchContext:
    """Retrieved sources for one turn, used to validate citation references."""

    sources: dict[str, SearchResult] = field(default_factory=dict)
    enabled_sources: frozenset[str] = SOURCE_NAMES


@dataclass
class OptionalArchiveToolset(WrapperToolset[SearchContext]):
    """Keep archive MCP outages from taking down report research."""

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
        self, ctx: RunContext[SearchContext]
    ) -> dict[str, ToolsetTool[SearchContext]]:
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

    async def call_tool(
        self, name: str, tool_args: dict, ctx: RunContext[SearchContext],
        tool: ToolsetTool[SearchContext],
    ):
        result = await super().call_tool(name, tool_args, ctx, tool)
        deps = getattr(ctx, "deps", None)
        enabled = getattr(deps, "enabled_sources", None) if deps is not None else None
        if enabled is not None and "ocha" not in enabled and name in ARCHIVE_SEARCH_TOOLS:
            result = _filter_ocha_payload(result)
        return result


archive_toolset = OptionalArchiveToolset(
    MCPToolset(
        archive_mcp_url,
        include_instructions=False,
        tool_error_behavior="failed",
    )
)


def search_reports(
    query: str,
    *,
    limit: int = 30,
    date_from: date | None = None,
    date_to: date | None = None,
    issue: int | None = None,
    report_title: str | None = None,
    search_fn: Callable[..., list[SearchResult]] = search,
) -> list[SearchResult]:
    """Search indexed OCHA report chunks, applying filters when provided."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if date_from is None and date_to is None and issue is None and report_title is None:
        return search_fn(query, limit=limit)
    return search_fn(
        query,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        issue=issue,
        report_title=report_title,
    )


def register_tools(agent, *, report_searcher, archive_toolset_getter) -> None:
    """Register research and mapping tools for the chat agent."""
    agent.tool_plain(mapping)

    @agent.toolset(per_run_step=False)
    def archive_for_run(ctx: RunContext[SearchContext]) -> OptionalArchiveToolset | None:
        if ctx.deps is None or "archive" in ctx.deps.enabled_sources:
            return archive_toolset_getter()
        return None

    async def prepare_ocha(
        ctx: RunContext[SearchContext], definition: ToolDefinition,
    ) -> ToolDefinition | None:
        if ctx.deps is None or "ocha" in ctx.deps.enabled_sources:
            return definition
        return None

    async def prepare_archive(
        ctx: RunContext[SearchContext], definition: ToolDefinition,
    ) -> ToolDefinition | None:
        if ctx.deps is None or "archive" in ctx.deps.enabled_sources:
            return definition
        return None

    @agent.tool(name=OCHA_TOOL_NAME, prepare=prepare_ocha)
    def search_ocha_corpus(
        ctx: RunContext[SearchContext], query: str, date_from: date | None = None,
        date_to: date | None = None, issue: int | None = None,
        report_title: str | None = None,
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
        results = report_searcher(
            query,
            date_from=date_from,
            date_to=date_to,
            issue=issue,
            report_title=report_title,
        )
        labelled = []
        for result in results:
            source_id = f"S{len(ctx.deps.sources) + 1}"
            ctx.deps.sources[source_id] = result
            labelled.append({"source_id": source_id, **result})
        return labelled

    @agent.tool(prepare=prepare_archive)
    def search_bearing_witness_document(
        ctx: RunContext[SearchContext], query: str, limit: int = 5,
    ) -> list[dict]:
        """Find relevant pages in the indexed Bearing Witness foundational document.

        Returns page citations and excerpts. Read matching pages with
        read_bearing_witness_pages before relying on document passages.
        """
        return search_document(query, limit=limit)

    @agent.tool(prepare=prepare_archive)
    def read_bearing_witness_pages(
        ctx: RunContext[SearchContext], start_page: int, end_page: int | None = None,
    ) -> dict:
        """Read full text from one or more indexed Bearing Witness document pages."""
        return read_document_pages(start_page, end_page)

    @agent.tool(prepare=prepare_archive)
    def search_faqs(
        ctx: RunContext[SearchContext], query: str, limit: int = 5,
    ) -> list[dict]:
        """Find Bearing Witness FAQ entries about the project, scope, method, or usage."""
        return bearing_witness_faqs.search(query, limit)

    @agent.tool(prepare=prepare_archive)
    def read_faq(ctx: RunContext[SearchContext], faq_id: str) -> dict:
        """Read the full FAQ answer for an ID returned by search_faqs."""
        return bearing_witness_faqs.read(faq_id)

    @agent.tool(prepare=prepare_archive)
    def list_project_metadata_records(ctx: RunContext[SearchContext]) -> list[dict]:
        """List Bearing Witness project, author, document, and website records."""
        return project_metadata.list_records()

    @agent.tool(prepare=prepare_archive)
    def read_project_metadata_record(
        ctx: RunContext[SearchContext], record_id: str,
    ) -> dict:
        """Read a Bearing Witness project metadata record by its record ID."""
        return project_metadata.read(record_id)
