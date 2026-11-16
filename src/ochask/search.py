"""Server-side hybrid retrieval over indexed reports."""
from __future__ import annotations

import math
import os
from dotenv import load_dotenv
from openai import OpenAI
from .ingest import embed_texts, rpc


def search(query: str, *, limit: int = 10, semantic_weight: float = 1.0,
           keyword_weight: float = 1.0):
    """Return ranked source chunks. Requires server-side OpenAI and Supabase credentials."""
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if any(not math.isfinite(w) or w < 0 for w in (semantic_weight, keyword_weight)) or not (semantic_weight or keyword_weight):
        raise ValueError("weights must be finite, non-negative, and not both zero")
    load_dotenv()
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key or not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    vector = embed_texts(OpenAI(), [query.strip()])[0]
    return rpc(url, key, "hybrid_search_report_chunks", {
        "query_text": query.strip(), "query_embedding": vector, "match_count": limit,
        "semantic_weight": semantic_weight, "keyword_weight": keyword_weight})
