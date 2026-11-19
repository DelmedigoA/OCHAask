from types import SimpleNamespace

import pytest

from ochask.bearing_witness_ingest import document_pages
from ochask.project_resources import FaqCollection, ProjectMetadataCollection


def test_document_pages_preserve_physical_page_numbers():
    assert document_pages("First page\f\fThird page\f  ") == [
        {"page": 1, "content": "First page"},
        {"page": 3, "content": "Third page"},
    ]


def test_document_pages_reject_empty_extraction():
    with pytest.raises(ValueError, match="no non-empty pages"):
        document_pages("\f \n\f")


def test_packaged_project_metadata_is_readable_and_cites_source_urls():
    metadata = ProjectMetadataCollection()
    records = metadata.list_records()

    assert records
    assert all("content" not in record for record in records)
    overview = metadata.read("project-overview")
    assert overview["source_url"] == "https://bearing-witness.com/about/"
    assert "long-term knowledge infrastructure" in overview["content"]


def test_faq_search_returns_question_then_read_returns_answer():
    faqs = FaqCollection()

    matches = faqs.search("project purpose and evidence", limit=3)
    assert matches
    selected = faqs.read(matches[0]["faq_id"])
    assert selected["question"] == matches[0]["question"]
    assert selected["answer"]
    assert selected["url"] == "https://bearing-witness.com/faq/"


def test_bearing_witness_document_search_returns_citable_page(monkeypatch):
    from ochask import bearing_witness_document as document

    monkeypatch.setattr(
        document.Settings,
        "from_environment",
        lambda: SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_secret_key="server-key",
            embedding_model="text-embedding-3-small",
        ),
    )
    monkeypatch.setattr(document, "OpenAI", lambda: object())
    monkeypatch.setattr(document, "embed_texts", lambda *_args, **_kwargs: [[0.1] * 1536])
    monkeypatch.setattr(
        document,
        "rpc",
        lambda *_args: [{
            "document_id": document.DOCUMENT_ID,
            "document_version": "v6.7.0",
            "title": "Bearing Witness - Gaza",
            "source_url": document.DOCUMENT_URL,
            "page": 12,
            "content": "This page describes the purpose of the document.",
            "fusion_score": 0.03,
        }],
    )

    result = document.search_document("purpose")

    assert result[0]["page"] == 12
    assert result[0]["source_url"] == document.DOCUMENT_URL
    assert "purpose" in result[0]["excerpt"]


def test_bearing_witness_page_read_is_bounded(monkeypatch):
    from ochask import bearing_witness_document as document

    monkeypatch.setattr(
        document.Settings,
        "from_environment",
        lambda: SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_secret_key="server-key",
            embedding_model="text-embedding-3-small",
        ),
    )
    calls = []
    monkeypatch.setattr(document, "rpc", lambda *_args: calls.append(_args[-1]) or [])

    assert document.read_document_pages(1, 11) == {
        "error": "Read no more than 10 pages at a time"
    }
    assert document.read_document_pages(1, 10)["error"] == "No indexed pages were found in that range"
    assert calls[0]["start_page"] == 1
    assert calls[0]["end_page"] == 10
