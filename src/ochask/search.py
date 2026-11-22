"""Server-side hybrid retrieval over indexed reports."""

from __future__ import annotations

import math
from datetime import date
from typing import TypedDict

from openai import OpenAI

from .config import Settings
from .database import rpc
from .embeddings import embed_texts

class SourceChunk(TypedDict):
    """An indexed chunk and the metadata needed to cite it."""

    chunk_id: str
    report_id: str
    title: str
    landing_url: str
    pages: list[int]
    content: str


class SearchResult(SourceChunk):
    """A source chunk ranked by reciprocal rank fusion."""

    fusion_score: float


def search(
    query: str,
    *,
    limit: int = 10,
    semantic_weight: float = 1.0,
    keyword_weight: float = 1.0,
    date_from: date | None = None,
    date_to: date | None = None,
    issue: int | None = None,
    report_title: str | None = None,
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
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must not be after date_to")
    if issue is not None and issue < 1:
        raise ValueError("issue must be positive")
    settings = Settings.from_environment()
    vector = embed_texts(OpenAI(), [query.strip()], settings.embedding_model)[0]
    if date_from is not None or date_to is not None or issue is not None or report_title:
        return _filtered_search(
            settings, query, vector, limit, date_from, date_to, issue, report_title,
            semantic_weight, keyword_weight,
        )
    return _search_unfiltered(settings, query.strip(), vector, limit, semantic_weight, keyword_weight)


def _search_unfiltered(settings, query, vector, limit, semantic_weight, keyword_weight):
    if settings.bm25_index_path and keyword_weight > 0:
        from .bm25 import fuse, load_index

        # The RPC returns at most 100 rows, even when its internal candidate pool is larger.
        candidate_limit = min(limit * 10, 100)
        keywords = load_index(settings).search(query, candidate_limit)
        semantic = (
            _rpc_search(settings, query, vector, candidate_limit, semantic_weight, 0)
            if semantic_weight > 0
            else []
        )
        return fuse(semantic, keywords, limit, semantic_weight, keyword_weight)
    return _rpc_search(settings, query, vector, limit, semantic_weight, keyword_weight)


def _rpc_search(settings, query, vector, limit, semantic_weight, keyword_weight):
    return rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "hybrid_search_report_chunks",
        {
            "query_text": query,
            "query_embedding": vector,
            "model_name": settings.embedding_model,
            "match_count": limit,
            "semantic_weight": semantic_weight,
            "keyword_weight": keyword_weight,
        },
    )


def _filtered_search(settings, query, vector, limit, date_from, date_to, issue, report_title, semantic_weight, keyword_weight):
    """Rank filtered chunks in Postgres without downloading their embeddings."""
    return rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "hybrid_search_filtered_report_chunks",
        {
            "query_text": query.strip(),
            "query_embedding": vector,
            "model_name": settings.embedding_model,
            "match_count": limit,
            "semantic_weight": semantic_weight,
            "keyword_weight": keyword_weight,
            "report_date_from": date_from.isoformat() if date_from else None,
            "report_date_to": date_to.isoformat() if date_to else None,
            "report_issue": issue,
            "report_title": report_title or None,
        },
    )
