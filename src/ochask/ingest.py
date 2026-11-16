"""Convert PDFs with Docling and atomically index contextualized chunks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .config import Settings
from .database import rpc
from .embeddings import embed_texts
from .models import IndexedChunk, NormalizedChunk
from .utils import (
    chunk_payload,
    chunk_source_pages,
    load_reports,
    report_id_from_path,
    report_payload,
    stable_chunk_id,
)

if TYPE_CHECKING:
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import DoclingDocument
    from openai import OpenAI


def make_converter() -> DocumentConverter:
    """Create the notebook’s Granite Docling VLM converter using MLX."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
    from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    options = VlmConvertOptions.from_preset(
        "granite_docling", engine_options=MlxVlmEngineOptions()
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=VlmPipelineOptions(vlm_options=options),
            )
        }
    )


def normalize_document(
    document: DoclingDocument,
    report_id: UUID,
    chunker: HybridChunker,
    token_count: Callable[[str], int],
) -> list[NormalizedChunk]:
    """Preserve contextualized text, deterministic IDs, and all known source pages."""
    records: list[NormalizedChunk] = []
    for index, chunk in enumerate(chunker.chunk(dl_doc=document)):
        text = chunker.contextualize(chunk).strip()
        if not text:
            continue
        pages = chunk_source_pages(chunk.model_dump())
        chunk_id = stable_chunk_id(report_id, index, text)
        records.append(
            NormalizedChunk(
                doc_id=report_id,
                chunk_id=chunk_id,
                chunk_index=index,
                from_pages=pages,
                text=text,
                n_tokens=token_count(text),
            )
        )
    return records


def embed_chunks(
    chunks: list[NormalizedChunk],
    client: OpenAI,
    model: str,
    batch_size: int = 64,
) -> list[IndexedChunk]:
    """Add embeddings to normalized chunks, preserving their order."""
    vectors = embed_texts(client, [chunk.text for chunk in chunks], model, batch_size)
    return [
        IndexedChunk(**chunk.model_dump(), embedding=vector)
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


def index_pdf(
    path: Path,
    report: dict[str, Any],
    converter: DocumentConverter,
    chunker: HybridChunker,
    token_count: Callable[[str], int],
    client: OpenAI,
    settings: Settings,
    batch_size: int = 64,
) -> int:
    """Convert, embed, and atomically replace one report's chunks."""
    report_id = report_id_from_path(path)
    if str(report_id) != str(report["id"]):
        raise ValueError("PDF and report IDs do not match")

    document = converter.convert(path).document
    chunks = normalize_document(document, report_id, chunker, token_count)
    if not chunks:
        raise ValueError("Document produced no chunks; existing index was not changed")

    indexed = embed_chunks(chunks, client, settings.embedding_model, batch_size)
    rpc(
        settings.supabase_url,
        settings.supabase_secret_key,
        "replace_report_chunks",
        {
            "report_data": report_payload(report),
            "chunks_data": [chunk_payload(chunk) for chunk in indexed],
            "model_name": settings.embedding_model,
        },
    )
    return len(indexed)


def main(argv: list[str] | None = None) -> int:
    """Run the indexing command and return a nonzero status on document failures."""
    import tiktoken
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
    from openai import OpenAI

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", type=Path, help="PDF file or directory")
    parser.add_argument(
        "--reports", type=Path, default=Path("data/scraped/landing_pages.jsonl")
    )
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_environment()
    except ValueError as exc:
        parser.error(str(exc))
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    reports = load_reports(args.reports)
    files = sorted(args.pdfs.glob("*.pdf")) if args.pdfs.is_dir() else [args.pdfs]
    if not files:
        parser.error("No PDFs found")
    # Reject duplicates before making any database changes.
    ids = [report_id_from_path(path) for path in files]
    if len(set(ids)) != len(ids):
        parser.error("Multiple PDFs have the same report ID")
    encoding = tiktoken.get_encoding("o200k_base")
    converter = make_converter()
    chunker = HybridChunker(
        tokenizer=OpenAITokenizer(tokenizer=encoding, max_tokens=450)
    )
    client = OpenAI()
    failures = 0
    for path, report_id in zip(files, ids):
        try:
            report = reports[str(report_id)]
            count = index_pdf(
                path,
                report,
                converter,
                chunker,
                lambda text: len(encoding.encode(text)),
                client,
                settings,
                args.batch_size,
            )
            print(f"{path.name}: indexed {count} chunks")
        except Exception as exc:
            failures += 1
            # Exceptions from third-party services can contain sensitive request data.
            print(
                f"{path.name}: indexing failed ({type(exc).__name__})", file=sys.stderr
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
