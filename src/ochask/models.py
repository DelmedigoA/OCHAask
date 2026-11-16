from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

# The Supabase vector column is fixed at this dimension.
EMBEDDING_DIMENSIONS = 1536


class MainImage(BaseModel):
    url: HttpUrl
    credit: str | None = None


class ReportAttachment(BaseModel):
    filename: str
    url: HttpUrl
    description: str | None = None
    file_type: str | None = None
    size: str | None = None
    language: str | None = None
    preview_url: HttpUrl | None = None


class ReportLanding(BaseModel):
    id: UUID
    landing_url: HttpUrl
    title: str
    report_type: str | None = None
    posted: date | None = None
    originally_published: date | None = None
    origin_url: HttpUrl | None = None
    main_image: MainImage | None = None
    key_points: list[str] = Field(default_factory=list)
    primary_country: str | None = None
    other_countries: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    attachments: list[ReportAttachment] = Field(default_factory=list)


class NormalizedChunk(BaseModel):
    """Contextualized Docling chunk and its source provenance."""

    doc_id: UUID
    chunk_id: UUID
    chunk_index: int = Field(ge=0)
    from_pages: set[int]
    text: str = Field(min_length=1)
    n_tokens: int = Field(ge=0)


class IndexedChunk(NormalizedChunk):
    """A normalized chunk with its embedding ready for indexing."""

    embedding: list[float] = Field(
        min_length=EMBEDDING_DIMENSIONS, max_length=EMBEDDING_DIMENSIONS
    )
