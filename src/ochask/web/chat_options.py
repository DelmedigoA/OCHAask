"""Server-owned chat settings and source-aware AG-UI history."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import groupby
from typing import Literal

from ag_ui.core import AssistantMessage, Message, ToolMessage, UserMessage
from pydantic import BaseModel, Field, StrictBool
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from ochask.chat import OCHA_TOOL_NAME, SOURCE_NAMES
from ochask.tools import ARCHIVE_RECORD_TOOLS, has_ocha_archive_records

SourceName = Literal["archive", "ocha"]
ReasoningEffort = Literal["low", "medium", "high"]
LOCAL_TOOLS = {"mapping", "search_location", "show_geo_map"}


class Sources(BaseModel, extra="forbid"):
    archive: StrictBool
    ocha: StrictBool


class ChatOptions(BaseModel, extra="forbid"):
    modelId: str | None = None
    reasoningEffort: ReasoningEffort | None = None
    sources: Sources = Field(default_factory=lambda: Sources(archive=True, ocha=True))
    priorTurnSources: dict[str, set[SourceName]] = Field(default_factory=dict)

    @property
    def enabled_sources(self) -> frozenset[str]:
        return frozenset(name for name in SOURCE_NAMES if getattr(self.sources, name))


def tool_source(name: str) -> SourceName | None:
    if name == OCHA_TOOL_NAME:
        return "ocha"
    if name in LOCAL_TOOLS:
        return None
    # The remaining server tools are discovered from the Archive MCP endpoint.
    return "archive"


def filter_history(
    messages: Sequence[Message], options: ChatOptions,
) -> tuple[list[Message], set[str]]:
    """Keep whole assistant turns so tool calls and results travel together."""
    retained: list[Message] = []
    retained_sources: set[str] = set()
    preceding_sources: set[str] = set()
    user_id: str | None = None
    active_sources = options.enabled_sources

    for is_user, group in groupby(messages, lambda message: isinstance(message, UserMessage)):
        if is_user:
            for message in group:
                retained.append(message)
                user_id = message.id
            continue

        turn = [
            message for message in group
            if message.role in {"assistant", "reasoning", "tool", "activity"}
        ]
        calls = {
            call.id: call.function.name
            for message in turn if isinstance(message, AssistantMessage)
            for call in message.tool_calls or ()
        }
        dependencies = {
            source for name in calls.values() if (source := tool_source(name)) is not None
        }
        for message in turn:
            if not isinstance(message, ToolMessage) or calls.get(message.tool_call_id) not in ARCHIVE_RECORD_TOOLS:
                continue
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                continue
            if has_ocha_archive_records(payload):
                dependencies.add("ocha")
        recorded = options.priorTurnSources.get(user_id) if user_id else None
        if recorded is not None:
            dependencies.update(recorded)
        elif turn:
            dependencies.update(preceding_sources)
            orphaned = any(
                isinstance(message, ToolMessage) and message.tool_call_id not in calls
                for message in turn
            )
            if orphaned or not dependencies:
                dependencies.update(SOURCE_NAMES)
        preceding_sources.update(dependencies)

        if dependencies <= active_sources:
            retained.extend(turn)
            retained_sources.update(dependencies)

    return retained, retained_sources


def run_tool_sources(messages: Sequence[ModelMessage]) -> set[SourceName]:
    sources = {
        source
        for message in messages if isinstance(message, ModelResponse)
        for part in message.parts if isinstance(part, ToolCallPart)
        if (source := tool_source(part.tool_name)) is not None
    }
    if any(
        isinstance(part, ToolReturnPart)
        and part.tool_name in ARCHIVE_RECORD_TOOLS
        and has_ocha_archive_records(part.content)
        for message in messages if isinstance(message, ModelRequest)
        for part in message.parts
    ):
        sources.add("ocha")
    return sources
