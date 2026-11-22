from ochask.config import Settings
from collections import Counter
from datetime import date

from ochask.corpus import _all_months, corpus_overview, search_evidence


SETTINGS = Settings("https://example.com", "sb_secret_test", "embedding-model")
REPORT_A = "00000000-0000-0000-0000-000000000001"
REPORT_B = "00000000-0000-0000-0000-000000000002"


def test_monthly_series_includes_every_month_across_the_full_date_span():
    months = _all_months(
        Counter({"2023-10": 3, "2024-01": 1, "2026-10": 2}),
        date(2023, 10, 7),
        date(2026, 10, 5),
    )

    assert len(months) == 37
    assert months[0] == {"month": "2023-10", "label": "Oct 23", "reports": 3}
    assert months[1]["month"] == "2023-11"
    assert months[1]["reports"] == 0
    assert months[-1] == {"month": "2026-10", "label": "Oct 26", "reports": 2}


def test_corpus_aggregates_chunk_tokens_pages_and_publication_months(monkeypatch):
    rows = {
        "report_landings": [
            {
                "id": REPORT_A,
                "title": "Report A",
                "posted": "2024-02-02",
                "originally_published": "2024-01-31",
                "landing_url": "https://reports.example/a",
                "origin_url": None,
            },
            {
                "id": REPORT_B,
                "title": "Report B",
                "posted": "2024-02-10",
                "originally_published": None,
                "landing_url": "https://reports.example/b",
                "origin_url": "https://ocha.example/b",
            },
        ],
        "report_attachments": [
            {
                "report_id": REPORT_A,
                "filename": "report-en.pdf",
                "url": "https://files.example/a-en.pdf",
                "file_type": "application/pdf",
                "language": "English",
            },
            {
                "report_id": REPORT_A,
                "filename": "report-ar.pdf",
                "url": "https://files.example/a-ar.pdf",
                "file_type": "application/pdf",
                "language": "Arabic",
            },
        ],
    }
    summaries = [
        {
            "report_id": REPORT_A,
            "chunk_token_count": 12,
            "token_count_complete": True,
        },
        {
            "report_id": REPORT_B,
            "chunk_token_count": 4,
            "token_count_complete": False,
        },
    ]
    rpc_calls = []

    monkeypatch.setattr("ochask.corpus.rest_rows", lambda _url, _key, table, **_kwargs: rows[table])
    monkeypatch.setattr(
        "ochask.corpus.rpc",
        lambda _url, _key, function, payload, **_kwargs: (
            rpc_calls.append((function, payload)) or summaries
        ),
    )
    data = corpus_overview(SETTINGS)

    assert rpc_calls == [
        ("summarize_report_embeddings", {"model_name": "embedding-model"})
    ]
    assert data["reports_count"] == 2
    assert data["chunk_token_count"] == 16
    assert data["chunk_token_count_complete"] is False
    assert data["date_start"] == "2024-01-31"
    assert data["date_end"] == "2024-02-10"
    assert {item["month"]: item["reports"] for item in data["monthly"] if item["reports"]} == {
        "2024-01": 1,
        "2024-02": 1,
    }
    reports = {item["id"]: item for item in data["reports"]}
    assert reports[REPORT_A]["token_count_complete"] is True
    assert reports[REPORT_B]["token_count_complete"] is False
    assert "pages_count" not in reports[REPORT_A]
    assert reports[REPORT_A]["source_url"] == "https://files.example/a-en.pdf"
    assert reports[REPORT_B]["source_url"] == "https://ocha.example/b"
    assert reports[REPORT_B]["publication_date"] == "2024-02-10"


def test_search_evidence_keeps_agent_order_and_adds_publication_date(monkeypatch):
    import importlib

    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai:gpt-4.1-mini")
    chat = importlib.import_module("ochask.chat")

    ranked = [
        {
            "chunk_id": "second",
            "report_id": REPORT_B,
            "title": "Report B",
            "landing_url": "https://reports.example/b",
            "pages": [2],
            "content": "Second ranked excerpt",
            "fusion_score": 0.04,
        },
        {
            "chunk_id": "first",
            "report_id": REPORT_A,
            "title": "Report A",
            "landing_url": "https://reports.example/a",
            "pages": [1],
            "content": "First ranked excerpt",
            "fusion_score": 0.03,
        },
    ]
    monkeypatch.setattr(chat, "search_reports", lambda _query, *, limit: ranked)
    monkeypatch.setattr(
        "ochask.corpus.rest_rows",
        lambda *_args, **_kwargs: [
            {"id": REPORT_A, "posted": None, "originally_published": "2024-01-31"},
            {"id": REPORT_B, "posted": "2024-02-10", "originally_published": None},
        ],
    )

    results = search_evidence("water access", 5, SETTINGS)

    assert [result["chunk_id"] for result in results] == ["second", "first"]
    assert [result["publication_date"] for result in results] == ["2024-02-10", "2024-01-31"]
