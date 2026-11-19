"""Structured relevance guard for the Bearing Witness Research Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 3
GUARD_TIMEOUT_SECONDS = 15
GUARDRAIL_MESSAGE = (
    "I can help with OCHA humanitarian reports and Bearing Witness research. "
    "Please keep questions on those topics and turn on OCHA for reports or "
    "Archive for Bearing Witness resources."
)
GUARD_UNAVAILABLE_MESSAGE = (
    "I can’t check that request right now. Please try again shortly."
)

GUARD_PROMPT = files("ochask").joinpath("prompts", "guardrail.md").read_text(encoding="utf-8").strip()


class GuardDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    continue_: bool = Field(alias="continue")
    relevance: int = Field(ge=1, le=10)
    reasoning: str = Field(max_length=36)


class RelevanceGuard:
    def __init__(
        self,
        model: str,
        *,
        relevance_threshold: int = RELEVANCE_THRESHOLD,
        timeout: float = GUARD_TIMEOUT_SECONDS,
    ) -> None:
        if not 1 <= relevance_threshold <= 10:
            raise ValueError("relevance_threshold must be between 1 and 10")
        self.relevance_threshold = relevance_threshold
        self.timeout = timeout
        self.agent = Agent(
            model,
            output_type=GuardDecision,
            system_prompt=GUARD_PROMPT,
            retries=0,
            defer_model_check=True,
        )

    async def check(
        self, question: str, recent_context: list[dict[str, str]] | None = None,
        enabled_sources: set[str] | frozenset[str] | None = None,
    ) -> tuple[bool, bool]:
        """Return (allowed, unavailable), failing closed on any guard error."""
        payload = {
            "recent_conversation": (recent_context or [])[-4:],
            "question": question[:6000],
            "enabled_sources": sorted(
                enabled_sources if enabled_sources is not None else {"archive", "ocha"}
            ),
            "minimum_relevance": self.relevance_threshold,
        }
        try:
            result = await asyncio.wait_for(
                self.agent.run(json.dumps(payload, ensure_ascii=False)),
                timeout=self.timeout,
            )
            decision = GuardDecision.model_validate(result.output)
        except Exception as exc:
            logger.warning("Bearing Witness relevance check unavailable: %s", type(exc).__name__)
            return False, True

        allowed = decision.continue_ and decision.relevance >= self.relevance_threshold
        logger.info(
            "Bearing Witness relevance check completed: allowed=%s relevance=%d",
            allowed,
            decision.relevance,
        )
        return allowed, False
