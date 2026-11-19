import asyncio
from types import SimpleNamespace

import pytest

from ochask.guard import RELEVANCE_THRESHOLD, GuardDecision, RelevanceGuard


@pytest.mark.parametrize(
    ("continue_decision", "relevance", "expected"),
    [
        (True, RELEVANCE_THRESHOLD, (True, False)),
        (True, RELEVANCE_THRESHOLD - 1, (False, False)),
        (False, 9, (False, False)),
    ],
)
def test_guard_requires_continue_and_threshold(
    monkeypatch,
    continue_decision,
    relevance,
    expected,
):
    guard = RelevanceGuard("test")

    async def fake_run(_payload):
        return SimpleNamespace(
            output=GuardDecision(
                **{
                    "continue": continue_decision,
                    "relevance": relevance,
                    "reasoning": "test",
                }
            )
        )

    monkeypatch.setattr(guard.agent, "run", fake_run)

    assert asyncio.run(guard.check("Question")) == expected


def test_guard_fails_closed_when_model_returns_invalid_decision(monkeypatch):
    guard = RelevanceGuard("test")

    async def fake_run(_payload):
        return SimpleNamespace(
            output={"continue": True, "relevance": 11, "reasoning": "invalid"}
        )

    monkeypatch.setattr(guard.agent, "run", fake_run)

    assert asyncio.run(guard.check("Question")) == (False, True)


def test_guard_receives_the_enabled_source_selection(monkeypatch):
    guard = RelevanceGuard("test")
    seen = []

    async def fake_run(payload):
        seen.append(payload)
        return SimpleNamespace(
            output=GuardDecision(
                **{"continue": True, "relevance": 8, "reasoning": "in scope"}
            )
        )

    monkeypatch.setattr(guard.agent, "run", fake_run)

    assert asyncio.run(guard.check("Question", enabled_sources={"archive"})) == (True, False)
    assert '"enabled_sources": ["archive"]' in seen[0]


def test_guard_rejects_invalid_threshold():
    with pytest.raises(ValueError, match="relevance_threshold"):
        RelevanceGuard("test", relevance_threshold=0)
