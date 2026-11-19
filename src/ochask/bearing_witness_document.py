"""Supabase-backed search and page reads for the Bearing Witness document."""

from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from .config import Settings
from .database import rpc
from .embeddings import embed_texts

DOCUMENT_ID = "bearing-witness-gaza-english"
DOCUMENT_VERSION = "v6.7.0"
DOCUMENT_TITLE = "Bearing Witness - Gaza, English v6.7.0, July 5 2025"
DOCUMENT_URL = "https://bearing-witness.com/bigger-picture/"


def _excerpt(text: str, query: str, length: int = 700) -> str:
    words = list(re.finditer(r"\w+", text))
    query_terms = set(re.findall(r"\w+", query.casefold()))
    match = next((word for word in words if word.group().casefold() in query_terms), None)
    start = max(0, match.start() - 180) if match else 0
    return text[start : start + length]


def search_document(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Return page matches and excerpts from the indexed Bearing Witness document."""
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")

    settings = Settings.from_environment()
    query_embedding = embed_texts(OpenAI(), [query], settings.embedding_model)[0]
    rows = rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "hybrid_search_bearing_witness_document",
        {
            "query_text": query,
            "query_embedding": query_embedding,
            "model_name": settings.embedding_model,
            "match_count": limit,
            "semantic_weight": 1,
            "keyword_weight": 1,
        },
    ) or []
    return [
        {
            "document_id": row["document_id"],
            "document_version": row["document_version"],
            "title": row["title"],
            "source_url": row["source_url"],
            "page": int(row["page"]),
            "excerpt": _excerpt(row["content"], query),
            "score": row["fusion_score"],
        }
        for row in rows
    ]


def read_document_pages(start_page: int, end_page: int | None = None) -> dict[str, Any]:
    """Read full text for a bounded physical page range from the indexed document."""
    end_page = start_page if end_page is None else end_page
    if start_page < 1 or end_page < start_page:
        return {"error": "Invalid page range"}
    if end_page - start_page > 9:
        return {"error": "Read no more than 10 pages at a time"}

    settings = Settings.from_environment()
    rows = rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "read_bearing_witness_document_pages",
        {
            "target_document_id": DOCUMENT_ID,
            "target_model": settings.embedding_model,
            "start_page": start_page,
            "end_page": end_page,
        },
    ) or []
    selected = sorted(
        (row for row in rows if start_page <= int(row["page"]) <= end_page),
        key=lambda row: int(row["page"]),
    )
    if not selected:
        return {"error": "No indexed pages were found in that range"}
    return {
        "document_id": selected[0]["document_id"],
        "document_version": selected[0]["document_version"],
        "title": selected[0]["title"],
        "source_url": selected[0]["source_url"],
        "start_page": int(selected[0]["page"]),
        "end_page": int(selected[-1]["page"]),
        "pages": [
            {"page": int(row["page"]), "text": row["content"]}
            for row in selected
        ],
    }
