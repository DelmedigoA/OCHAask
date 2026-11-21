from datetime import date

from ochask import search as retrieval
from ochask.config import Settings

TITLE = "Gaza Humanitarian Response | Situation Report No. 58"
RESULT = {
    "chunk_id": "c1",
    "report_id": "r1",
    "title": TITLE,
    "landing_url": "https://example.test/situation-report-no-58",
    "pages": [1],
    "content": "The January 2026 ration was two food parcels.",
    "fusion_score": 0.5,
}


def install_fake_search(monkeypatch, *, known_titles):
    settings = Settings("https://example.test", "key", "model")
    calls = []

    def fake_filtered_search(
        _settings,
        _query,
        _vector,
        _limit,
        date_from,
        date_to,
        issue,
        report_title,
        _semantic_weight,
        _keyword_weight,
    ):
        calls.append({
            "report_title": report_title,
            "report_issue": issue,
            "report_date_from": date_from,
            "report_date_to": date_to,
        })
        if report_title is None:
            return [dict(RESULT)] if known_titles else []
        if not any(report_title.lower() in title.lower() for title in known_titles):
            return []
        return [dict(RESULT)]

    monkeypatch.setattr(retrieval, "_filtered_search", fake_filtered_search)
    monkeypatch.setattr(retrieval, "OpenAI", lambda: None)
    monkeypatch.setattr(retrieval, "embed_texts", lambda *args: [[0.5]])
    monkeypatch.setattr(Settings, "from_environment", lambda: settings)
    return calls


def test_report_title_filter_falls_back_when_substring_misses(monkeypatch):
    calls = install_fake_search(monkeypatch, known_titles=[TITLE])

    hits = retrieval.search(
        "Situation Report No. 58 January 2026 food ration",
        date_from=date(2026, 1, 1),
        issue=58,
        report_title="Gaza Humanitarian Response Situation Report No. 58",
    )

    assert [hit["chunk_id"] for hit in hits] == ["c1"]
    assert [call["report_title"] for call in calls] == [
        "Gaza Humanitarian Response Situation Report No. 58",
        None,
    ]
    # The fallback keeps the date and issue filters.
    assert calls[1]["report_date_from"] == date(2026, 1, 1)
    assert calls[1]["report_issue"] == 58


def test_report_title_filter_does_not_retry_when_title_matches(monkeypatch):
    calls = install_fake_search(monkeypatch, known_titles=[TITLE])

    hits = retrieval.search(
        "Situation Report No. 58 January 2026 food ration",
        report_title="Situation Report No. 58",
    )

    assert [hit["chunk_id"] for hit in hits] == ["c1"]
    assert [call["report_title"] for call in calls] == ["Situation Report No. 58"]


def test_report_title_filter_uses_issue_hint_from_query_when_title_misses(monkeypatch):
    calls = install_fake_search(monkeypatch, known_titles=[TITLE])

    hits = retrieval.search(
        "Situation Report No. 58 January 2026 food ration",
        report_title="Gaza Humanitarian Response Situation Report",
    )

    assert [hit["chunk_id"] for hit in hits] == ["c1"]
    assert [call["report_title"] for call in calls] == [
        "Gaza Humanitarian Response Situation Report",
        None,
    ]
    assert [call["report_issue"] for call in calls] == [None, 58]


def test_report_title_fallback_returns_empty_when_corpus_has_no_match(monkeypatch):
    calls = install_fake_search(monkeypatch, known_titles=[])

    hits = retrieval.search(
        "Situation Report No. 99",
        report_title="Gaza Humanitarian Response Situation Report No. 99",
    )

    assert hits == []
    assert [call["report_title"] for call in calls] == [
        "Gaza Humanitarian Response Situation Report No. 99",
        None,
        None,
    ]
    assert [call["report_issue"] for call in calls] == [None, 99, None]
