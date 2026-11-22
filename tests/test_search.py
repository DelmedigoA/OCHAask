from datetime import date

import pytest

from ochask import search as retrieval
from ochask.config import Settings


@pytest.fixture
def filtered_search(monkeypatch):
    settings = Settings("https://example.test", "key", "model")
    calls = []
    results = [{
        "chunk_id": "chunk-1",
        "report_id": "gaza",
        "title": "Gaza Situation Report No. 58",
        "landing_url": "https://example.test/gaza",
        "pages": [1],
        "content": "Food ration",
        "fusion_score": 0.03,
    }]

    def rpc(_url, _key, function, payload, **_kwargs):
        calls.append((function, payload))
        return results

    def unexpected_rest_rows(*_args, **_kwargs):
        raise AssertionError("filtered search must not download report embeddings")

    monkeypatch.setattr(retrieval, "rpc", rpc)
    monkeypatch.setattr(retrieval, "rest_rows", unexpected_rest_rows, raising=False)
    monkeypatch.setattr(retrieval, "OpenAI", lambda: None)
    monkeypatch.setattr(retrieval, "embed_texts", lambda *args: [[0.5]])
    monkeypatch.setattr(Settings, "from_environment", lambda: settings)
    return calls, results


@pytest.mark.parametrize("title", [
    "Gaza Humanitarian Response Situation Report No. 58",
    "gaza humanitarian response — situation report no 58",
    "Situation Report No. 58",
])
def test_filtered_search_uses_database_rpc_without_fetching_embeddings(filtered_search, title):
    calls, results = filtered_search

    hits = retrieval.search(
        "  January 2026 food ration  ",
        limit=7,
        semantic_weight=0.5,
        keyword_weight=1.5,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        issue=58,
        report_title=title,
    )

    assert hits == results
    assert calls == [(
        "hybrid_search_filtered_report_chunks",
        {
            "query_text": "January 2026 food ration",
            "query_embedding": [0.5],
            "model_name": "model",
            "match_count": 7,
            "semantic_weight": 0.5,
            "keyword_weight": 1.5,
            "report_date_from": "2026-01-01",
            "report_date_to": "2026-01-31",
            "report_issue": 58,
            "report_title": title,
        },
    )]


def test_title_only_filter_uses_database_rpc(filtered_search):
    calls, results = filtered_search

    assert retrieval.search("food ration", report_title="Situation Report No. 58") == results
    assert calls[0][0] == "hybrid_search_filtered_report_chunks"
    assert calls[0][1]["report_date_from"] is None
    assert calls[0][1]["report_date_to"] is None
    assert calls[0][1]["report_issue"] is None
    assert calls[0][1]["report_title"] == "Situation Report No. 58"
