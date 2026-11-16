"""Convert PDFs with Docling and atomically index contextualized chunks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

import requests
from dotenv import load_dotenv

from .models import IndexedChunk, NormalizedChunk


def report_id_from_path(path: Path) -> UUID:
    try:
        return UUID(path.name.split("_", 1)[0])
    except ValueError as exc:
        raise ValueError(f"PDF filename must start with a report UUID: {path.name}") from exc


def make_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
    from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline
    options = VlmConvertOptions.from_preset("granite_docling", engine_options=MlxVlmEngineOptions())
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(
        pipeline_cls=VlmPipeline, pipeline_options=VlmPipelineOptions(vlm_options=options))})


def normalize_document(document, report_id: UUID, chunker, token_count) -> list[NormalizedChunk]:
    records = []
    for index, chunk in enumerate(chunker.chunk(dl_doc=document)):
        text = chunker.contextualize(chunk).strip()
        if not text:
            continue
        pages = set()
        for item in chunk.model_dump().get("meta", {}).get("doc_items", []):
            for provenance in item.get("prov", []):
                page = provenance.get("page_no")
                if isinstance(page, int) and page > 0:
                    pages.add(page)
        # Chunk order is part of identity, so reruns with identical output remain stable.
        chunk_id = uuid5(NAMESPACE_URL, f"ochask:{report_id}:{index}:{text}")
        records.append(NormalizedChunk(doc_id=report_id, chunk_id=chunk_id, chunk_index=index,
                                       from_pages=pages, text=text, n_tokens=token_count(text)))
    return records


def embed_texts(client, texts, model, batch_size=64):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(batch))):
            raise ValueError("Embedding response did not contain exactly one vector per input")
        for item in ordered:
            if len(item.embedding) != 1536:
                raise ValueError("Unexpected embedding dimensions")
            vectors.append(item.embedding)
    return vectors


def report_payload(report):
    image = report.get("main_image") or {}
    return {"id": report["id"], "landing_url": report["landing_url"], "title": report["title"],
            "report_type": report.get("report_type"), "posted": report.get("posted"),
            "originally_published": report.get("originally_published"), "origin_url": report.get("origin_url"),
            "main_image_url": image.get("url"), "main_image_credit": image.get("credit"),
            "key_points": report.get("key_points") or [],
            "metadata": {key: report.get(key) for key in ("primary_country", "other_countries", "sources", "formats", "themes")}}


def rpc(url, key, function, payload, session=requests):
    response = session.post(f"{url.rstrip('/')}/rest/v1/rpc/{function}",
                            headers={"apikey": key, "Authorization": f"Bearer {key}",
                                     "Content-Type": "application/json"}, json=payload, timeout=120)
    # Avoid printing request headers or response bodies that could contain sensitive data.
    if not response.ok:
        raise RuntimeError(f"Supabase {function} failed (HTTP {response.status_code})")
    return response.json() if response.content else None


def index_pdf(path, report, converter, chunker, token_count, client, url, key, model, batch_size=64):
    report_id = report_id_from_path(path)
    if str(report_id) != str(report["id"]):
        raise ValueError("PDF and report IDs do not match")
    document = converter.convert(path).document
    chunks = normalize_document(document, report_id, chunker, token_count)
    if not chunks:
        raise ValueError("Document produced no non-empty chunks; existing index was not changed")
    vectors = embed_texts(client, [chunk.text for chunk in chunks], model, batch_size)
    indexed = [IndexedChunk(**chunk.model_dump(), embedding=vector)
               for chunk, vector in zip(chunks, vectors, strict=True)]
    payload = [{"chunk_id": str(chunk.chunk_id), "chunk_index": chunk.chunk_index,
                "content": chunk.text, "pages": sorted(chunk.from_pages),
                "token_count": chunk.n_tokens, "embedding": chunk.embedding}
               for chunk in indexed]
    rpc(url, key, "replace_report_chunks", {"report_data": report_payload(report), "chunks_data": payload, "model_name": model})
    return len(chunks)


def main(argv=None):
    import os
    import tiktoken
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
    from openai import OpenAI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", type=Path, help="PDF file or directory")
    parser.add_argument("--reports", type=Path, default=Path("data/scraped/landing_pages.jsonl"))
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)
    load_dotenv()
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "").strip()
    if not url or not key or not os.getenv("OPENAI_API_KEY") or not model:
        parser.error("OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    reports = {}
    with args.reports.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                report = json.loads(line)
                if report["id"] in reports:
                    raise ValueError(f"Duplicate report ID: {report['id']}")
                reports[report["id"]] = report
    files = sorted(args.pdfs.glob("*.pdf")) if args.pdfs.is_dir() else [args.pdfs]
    if not files:
        parser.error("No PDFs found")
    # Reject duplicates before making any database changes.
    ids = [report_id_from_path(path) for path in files]
    if len(set(ids)) != len(ids):
        parser.error("Multiple PDFs have the same report ID")
    encoding = tiktoken.get_encoding("o200k_base")
    converter = make_converter()
    chunker = HybridChunker(tokenizer=OpenAITokenizer(tokenizer=encoding, max_tokens=450))
    client = OpenAI()
    failures = 0
    for path, report_id in zip(files, ids):
        try:
            report = reports[str(report_id)]
            count = index_pdf(path, report, converter, chunker, lambda text: len(encoding.encode(text)),
                              client, url, key, model, args.batch_size)
            print(f"{path.name}: indexed {count} chunks")
        except Exception as exc:
            failures += 1
            # Exceptions from third-party services can contain sensitive request data.
            print(f"{path.name}: indexing failed ({type(exc).__name__})", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
