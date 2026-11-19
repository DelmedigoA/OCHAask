"""Embed form-feed-separated Bearing Witness pages into Supabase."""

from __future__ import annotations

import argparse
from pathlib import Path

from openai import OpenAI

from .bearing_witness_document import (
    DOCUMENT_ID,
    DOCUMENT_TITLE,
    DOCUMENT_URL,
    DOCUMENT_VERSION,
)
from .config import Settings
from .database import rpc
from .embeddings import embed_texts
from .models import EMBEDDING_DIMENSIONS

DEFAULT_SOURCE_URL = DOCUMENT_URL


def document_pages(text: str) -> list[dict[str, int | str]]:
    """Split extracted PDF text while preserving original physical page numbers."""
    pages = [
        {"page": page_number, "content": content.strip()}
        for page_number, content in enumerate(text.split("\f"), start=1)
        if content.strip()
    ]
    if not pages:
        raise ValueError("The document text has no non-empty pages")
    return pages


def ingest(
    text_path: Path,
    *,
    version: str = DOCUMENT_VERSION,
    title: str | None = None,
    source_url: str = DEFAULT_SOURCE_URL,
) -> int:
    """Embed and atomically replace the current document pages for this model."""
    if not text_path.is_file():
        raise FileNotFoundError(text_path)
    text = text_path.read_text(encoding="utf-8", errors="replace")
    pages = document_pages(text)
    title = title or (
        DOCUMENT_TITLE
        if version == DOCUMENT_VERSION
        else f"Bearing Witness - Gaza, English {version}"
    )
    settings = Settings.from_environment()
    embedding_inputs = [
        f"{title} page {page['page']}\n{page['content']}" for page in pages
    ]
    embeddings = embed_texts(
        OpenAI(), embedding_inputs, settings.embedding_model, batch_size=32
    )
    if any(len(embedding) != EMBEDDING_DIMENSIONS for embedding in embeddings):
        raise ValueError("Unexpected embedding dimensions")

    rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "replace_bearing_witness_document_pages",
        {
            "target_document_id": DOCUMENT_ID,
            "target_version": version,
            "target_title": title,
            "target_source_url": source_url,
            "pages_data": [
                {**page, "embedding": embedding}
                for page, embedding in zip(pages, embeddings, strict=True)
            ],
            "model_name": settings.embedding_model,
        },
    )
    return len(pages)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index the Bearing Witness document pages in Supabase."
    )
    parser.add_argument("text", type=Path, help="Extracted text with form-feed page separators")
    parser.add_argument("--version", default=DOCUMENT_VERSION, help="Document version label")
    parser.add_argument("--title", help="Document title (defaults to Bearing Witness - Gaza, English VERSION)")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    args = parser.parse_args()
    count = ingest(args.text, version=args.version, title=args.title, source_url=args.source_url)
    print(f"Indexed {count} Bearing Witness pages for embedding model {Settings.from_environment().embedding_model}.")


if __name__ == "__main__":
    main()
