"""Tool definitions and toolsets for the Bearing Witness Research Assistant."""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlsplit

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
ARCHIVE_RECORD_TOOLS = frozenset({"search_archive", "lookup_url", "request_archiving"})
_OCHA_NAME = re.compile(r"\b(?:UNOCHA|OCHA|(?:UN |United Nations )?Office for the Coordination of Humanitarian Affairs)\b", re.I)
_OCHA_DOMAINS = ("ochaopt.org", "unocha.org", "ocha.un.org")


def _is_ocha_provenance(item: object) -> bool:
    """Return True when an archive item is an OCHA document or archived copy."""
    if not isinstance(item, dict):
        return False
    if any(_OCHA_NAME.search(str(item.get(field, ""))) for field in ("platform", "author")):
        return True
    for field in ("url", "page_url"):
        try:
            host = (urlsplit(str(item.get(field) or "")).hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if any(host == domain or host.endswith(f".{domain}") for domain in _OCHA_DOMAINS):
            return True
    return False


def has_ocha_archive_records(payload: object) -> bool:
    if isinstance(payload, list):
        return any(has_ocha_archive_records(item) for item in payload)
    if isinstance(payload, dict):
        return _is_ocha_provenance(payload) or any(
            has_ocha_archive_records(payload.get(key))
            for key in ("record", "cataloged", "unindexed", "hits")
        )
    return False


def _filter_ocha_payload(payload):
    """Remove OCHA-provenance items from an archive search result.

    The archive indexes copies of OCHA documents. When the OCHA source is
    disabled those copies must not contribute evidence, so they are dropped
    before the model sees the result.
    """
    if isinstance(payload, list):
        return [item for item in payload if not has_ocha_archive_records(item)]
    if not isinstance(payload, dict):
        return payload
    if _is_ocha_provenance(payload) or _is_ocha_provenance(payload.get("record")):
        return {"error": "This archived item belongs to the disabled OCHA source."}
    result = dict(payload)
    for key in ("cataloged", "unindexed"):
        if isinstance(result.get(key), dict):
            result[key] = _filter_ocha_payload(result[key])
    if isinstance(result.get("hits"), list):
        result["hits"] = _filter_ocha_payload(result["hits"])
        # The upstream count includes disabled results, including on other pages.
        result.pop("total", None)
    return result


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
        if enabled is not None and "ocha" not in enabled and name in ARCHIVE_RECORD_TOOLS:
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


def _publication_date(row: dict) -> date | None:
    for field in ("originally_published", "posted"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _indexed_coverage() -> dict[str, str] | None:
    """Return the indexed publication range, or None when it is unavailable."""
    try:
        from .config import Settings
        from .database import rest_rows

        settings = Settings.from_environment()
        rows = rest_rows(
            settings.supabase_url,
            settings.supabase_secret_key,
            "report_landings",
            select="originally_published,posted",
        )
    except Exception as exc:
        logger.info("Indexed corpus coverage unavailable: %s", type(exc).__name__)
        return None
    dates = [published for row in rows if (published := _publication_date(row))]
    if not dates:
        return None
    return {
        "publication_start": min(dates).isoformat(),
        "publication_end": max(dates).isoformat(),
    }


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
    ) -> list[dict] | dict:
        """Search report chunks and return citation IDs, text and source pages.

        When the user specifies a publication date range or an issue number, pass
        those filters. When a specific series or location is supplied, pass its title fragment in report_title.

        Args:
            query: Evidence to find, including the report series or location if given.
            date_from: Inclusive earliest publication date (YYYY-MM-DD), if specified.
            date_to: Inclusive latest publication date (YYYY-MM-DD), if specified.
            issue: Issue number, for example 300 for #300, if specified.
            report_title: Title fragment identifying the report series or location, if specified.
        """
        results = report_searcher(
            query,
            date_from=date_from,
            date_to=date_to,
            issue=issue,
            report_title=report_title,
        )
        if not results and (date_from is not None or date_to is not None):
            coverage = _indexed_coverage()
            if coverage:
                return {
                    "results": [],
                    "corpus_coverage": coverage,
                    "note": (
                        "No indexed report matched these filters. The indexed corpus "
                        f"covers publications from {coverage['publication_start']} to "
                        f"{coverage['publication_end']}. If the requested period falls "
                        "after that range, tell the user the period is outside the "
                        "indexed corpus and give the latest indexed publication date. "
                        "Do not estimate a figure."
                    ),
                }
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
