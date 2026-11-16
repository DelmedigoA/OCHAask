"""Server-side hybrid retrieval over indexed reports."""

from __future__ import annotations

import math
from typing import TypedDict

from openai import OpenAI

from .config import Settings
from .database import rpc
from .embeddings import embed_texts


class SearchResult(TypedDict):
    """A ranked source chunk and the metadata needed to cite it."""

    chunk_id: str
    report_id: str
    title: str
    landing_url: str
    pages: list[int]
    content: str
    fusion_score: float


def search(
    query: str,
    *,
    limit: int = 10,
    semantic_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> list[SearchResult]:
    """Return ranked source chunks. Requires server-side OpenAI and Supabase credentials."""
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if any(
        not math.isfinite(w) or w < 0 for w in (semantic_weight, keyword_weight)
    ) or not (semantic_weight or keyword_weight):
        raise ValueError("weights must be finite, non-negative, and not both zero")
    settings = Settings.from_environment()
    vector = embed_texts(OpenAI(), [query.strip()], settings.embedding_model)[0]
    return rpc(
        settings.supabase_url,
        settings.supabase_service_role_key,
        "hybrid_search_report_chunks",
        {
            "query_text": query.strip(),
            "query_embedding": vector,
            "model_name": settings.embedding_model,
            "match_count": limit,
            "semantic_weight": semantic_weight,
            "keyword_weight": keyword_weight,
        },
    )
