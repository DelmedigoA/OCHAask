"""Helpers for report identities, provenance, and database serialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .models import IndexedChunk


def report_id_from_path(path: Path) -> UUID:
    """Extract the report ID from the downloader’s UUID-prefixed filename."""
    try:
        return UUID(path.name.split("_", 1)[0])
    except ValueError as exc:
        raise ValueError(
            f"PDF filename must start with a report UUID: {path.name}"
        ) from exc


def chunk_source_pages(chunk_data: dict[str, Any]) -> set[int]:
    """Collect positive, one-based page numbers from Docling provenance."""
    pages: set[int] = set()
    for item in chunk_data.get("meta", {}).get("doc_items", []):
        for provenance in item.get("prov", []):
            page = provenance.get("page_no")
            if isinstance(page, int) and page > 0:
                pages.add(page)
    return pages


def stable_chunk_id(report_id: UUID, index: int, text: str) -> UUID:
    """Identify identical contextualized output consistently across reruns."""
    return uuid5(NAMESPACE_URL, f"ochask:{report_id}:{index}:{text}")


def report_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Map scraped landing metadata to the database report fields."""
    image = report.get("main_image") or {}
    return {
        "id": report["id"],
        "landing_url": report["landing_url"],
        "title": report["title"],
        "report_type": report.get("report_type"),
        "posted": report.get("posted"),
        "originally_published": report.get("originally_published"),
        "origin_url": report.get("origin_url"),
        "main_image_url": image.get("url"),
        "main_image_credit": image.get("credit"),
        "key_points": report.get("key_points") or [],
        "metadata": {
            key: report.get(key)
            for key in (
                "primary_country",
                "other_countries",
                "sources",
                "formats",
                "themes",
            )
        },
    }


def chunk_payload(chunk: IndexedChunk) -> dict[str, Any]:
    """Convert a validated chunk to the database RPC's JSON field names."""
    return {
        "chunk_id": str(chunk.chunk_id),
        "chunk_index": chunk.chunk_index,
        "content": chunk.text,
        "pages": sorted(chunk.from_pages),
        "token_count": chunk.n_tokens,
        "embedding": chunk.embedding,
    }
