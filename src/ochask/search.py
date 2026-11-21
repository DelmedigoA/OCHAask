"""Server-side hybrid retrieval over indexed reports."""

from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import TypedDict

from openai import OpenAI

from .config import Settings
from .database import rest_rows, rpc
from .embeddings import embed_texts

logger = logging.getLogger(__name__)


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


_ISSUE_PATTERN = re.compile(r"(?:#|\bno\.?)\s*(\d+)\b", re.IGNORECASE)


def _issue_from_text(*texts: str | None) -> int | None:
    """Return an issue number written as "#58" or "No. 58" in any text."""
    for text in texts:
        if not text:
            continue
        match = _ISSUE_PATTERN.search(text)
        if match:
            return int(match.group(1))
    return None


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
        results = _filtered_search(settings, query, vector, limit, date_from, date_to, issue, report_title, semantic_weight, keyword_weight)
        if not results and report_title:
            # The database matches titles as exact substrings, so a natural
            # title can miss when the stored title adds separators such as "|".
            # Treat the title as a hint: retry with an issue number when one is
            # present, then without the title, before reporting no matches.
            issue_hint = issue if issue is not None else _issue_from_text(
                report_title, query
            )
            if issue_hint is not None:
                logger.info(
                    "Report title filter matched nothing; retrying with issue %s",
                    issue_hint,
                )
                results = _filtered_search(settings, query, vector, limit, date_from, date_to, issue_hint, None, semantic_weight, keyword_weight)
            if not results and issue_hint != issue:
                logger.info("Report title filter matched nothing; retrying without it")
                results = _filtered_search(settings, query, vector, limit, date_from, date_to, issue, None, semantic_weight, keyword_weight)
        return results
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
    """Rank chunks only after restricting reports; no database schema change required."""
    from .corpus import _publication_date

    reports = rest_rows(settings.supabase_url, settings.supabase_secret_key, "report_landings",
                        select="id,title,landing_url,posted,originally_published")
    issue_pattern = re.compile(r"(?:#|\bNo\.?)\s*" + re.escape(str(issue)) + r"\b", re.I) if issue is not None else None
    selected = {}
    for report in reports:
        published = _publication_date(report)
        if (date_from or date_to) and (not published or (date_from and published < date_from) or (date_to and published > date_to)):
            continue
        if report_title and report_title.casefold() not in (report.get("title") or "").casefold():
            continue
        if issue_pattern and not issue_pattern.search(report.get("title") or ""):
            continue
        selected[report["id"]] = report
    if not selected:
        return []
    ids = list(selected)

    def fetch_batch(batch):
        return rest_rows(settings.supabase_url, settings.supabase_secret_key, "report_embeddings",
            select="chunk_id,report_id,pages,content,embedding",
            filters={"report_id": "in.(" + ",".join(batch) + ")",
                     "embedding_model": "eq." + settings.embedding_model, "chunk_id": "not.is.null"})

    batches = [ids[offset:offset + 100] for offset in range(0, len(ids), 100)]
    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as executor:
        chunks = [chunk for batch in executor.map(fetch_batch, batches) for chunk in batch]
    terms = set(re.findall(r"\w+", query.lower()))
    ranked = []
    vector_norm = math.sqrt(sum(x * x for x in vector))
    for chunk in chunks:
        embedding = chunk.get("embedding")
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        if not embedding or len(embedding) != len(vector):
            continue
        norm = math.sqrt(sum(x * x for x in embedding))
        similarity = sum(a * b for a, b in zip(vector, embedding)) / (vector_norm * norm) if norm and vector_norm else 0
        words = set(re.findall(r"\w+", (chunk.get("content") or "").lower()))
        overlap = len(terms & words) / len(terms) if terms else 0
        ranked.append((chunk, similarity, overlap))
    semantic = {row[0]["chunk_id"]: i for i, row in enumerate(sorted(ranked, key=lambda r: r[1], reverse=True), 1)}
    keyword = {row[0]["chunk_id"]: i for i, row in enumerate(sorted((r for r in ranked if r[2] > 0), key=lambda r: r[2], reverse=True), 1)}
    results = []
    for chunk, _, _ in ranked:
        key = chunk["chunk_id"]
        score = semantic_weight / (60 + semantic[key]) + (keyword_weight / (60 + keyword[key]) if key in keyword else 0)
        report = selected[chunk["report_id"]]
        results.append({"chunk_id": key, "report_id": chunk["report_id"], "title": report["title"],
                        "landing_url": report["landing_url"], "pages": chunk["pages"], "content": chunk["content"], "fusion_score": score})
    return sorted(results, key=lambda result: result["fusion_score"], reverse=True)[:limit]
