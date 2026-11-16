from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


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
