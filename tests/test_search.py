from datetime import date

import pytest

from ochask import search as retrieval
from ochask.config import Settings


@pytest.fixture
def indexed_reports(monkeypatch):
    settings = Settings("https://example.test", "key", "model")
    selected = []
    reports = [
        {"id": "gaza", "title": "Gaza Humanitarian Response | Situation Report No. 58", "posted": "2026-01-04"},
        {"id": "other", "title": "Another Series — Situation Report No. 58", "posted": "2026-01-04"},
        {"id": "earlier", "title": "Gaza Humanitarian Response | Situation Report No. 57", "posted": "2025-12-28"},
    ]
    for report in reports:
        report["landing_url"] = f"https://example.test/{report['id']}"

    def rows(_url, _key, table, *, select, filters=None):
        if table == "report_landings":
            return reports
        ids = filters["report_id"].removeprefix("in.(").removesuffix(")").split(",")
        selected.extend(ids)
        return [
            {"chunk_id": report_id, "report_id": report_id, "pages": [1], "content": "Food ration", "embedding": [0.5]}
            for report_id in ids
        ]

    monkeypatch.setattr(retrieval, "rest_rows", rows)
    monkeypatch.setattr(retrieval, "OpenAI", lambda: None)
    monkeypatch.setattr(retrieval, "embed_texts", lambda *args: [[0.5]])
    monkeypatch.setattr(Settings, "from_environment", lambda: settings)
    return selected


@pytest.mark.parametrize("title", [
    "Gaza Humanitarian Response Situation Report No. 58",
    "gaza humanitarian response — situation report no 58",
    "Situation Report No. 58",
])
def test_report_title_tolerates_separators_and_keeps_date_and_issue(indexed_reports, title):
    hits = retrieval.search(
        "January 2026 food ration",
        date_from=date(2026, 1, 1), issue=58, report_title=title,
    )
    expected = ["gaza", "other"] if title.startswith("Situation Report") else ["gaza"]
    assert [hit["report_id"] for hit in hits] == expected
    assert indexed_reports == expected


@pytest.mark.parametrize("title", ["Absent Series Situation Report No. 58", "Situation Report No. 5", "|"])
def test_missing_report_title_does_not_fetch_a_different_report(indexed_reports, title):
    assert retrieval.search("January 2026 food ration", report_title=title) == []
    assert indexed_reports == []


def test_normalized_title_does_not_override_an_explicit_issue_or_date(indexed_reports):
    title = "Gaza Humanitarian Response Situation Report"
    assert retrieval.search("food ration", report_title=title, issue=99) == []
    assert retrieval.search("food ration", report_title=title, date_from=date(2026, 2, 1)) == []
    assert indexed_reports == []
