"""Resume-aware full-corpus PDF indexing and private-storage upload command."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import requests

from .config import Settings
from .ingest import index_pdf, make_converter
from .storage import upload_pdf
from .utils import load_reports, report_id_from_path

PAGE_SIZE = 1000


def fetch_indexed_report_ids(settings: Settings) -> set[str]:
    """List reports already indexed for the configured model, paging safely."""
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/report_embeddings"
    headers = {"apikey": settings.supabase_secret_key}
    found: set[str] = set()
    offset = 0
    while True:
        response = requests.get(
            url,
            headers={**headers, "Range-Unit": "items", "Range": f"{offset}-{offset + PAGE_SIZE - 1}"},
            params={"select": "report_id", "embedding_model": f"eq.{settings.embedding_model}"},
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Could not read index status (HTTP {response.status_code})")
        rows = response.json()
        found.update(row["report_id"] for row in rows)
        if len(rows) < PAGE_SIZE:
            return found
        offset += len(rows)


def fetch_pdf_links(settings: Settings) -> dict[str, dict[str, Any]]:
    """Read report-to-PDF associations, including stored content hashes."""
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/report_landings"
    headers = {"apikey": settings.supabase_secret_key}
    links: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        response = requests.get(
            url,
            headers={**headers, "Range-Unit": "items", "Range": f"{offset}-{offset + PAGE_SIZE - 1}"},
            params={"select": "id,pdf_storage_path,pdf_sha256"},
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Could not read PDF link status (HTTP {response.status_code})")
        rows = response.json()
        links.update((row["id"], row) for row in rows)
        if len(rows) < PAGE_SIZE:
            return links
        offset += len(rows)


def pdf_is_linked_and_present(
    report_id: UUID,
    digest: str,
    links: dict[str, dict[str, Any]],
    settings: Settings,
) -> bool:
    """Check the linked hash and confirm the private Storage object exists."""
    expected_path = f"reports/{report_id}/{digest}.pdf"
    row = links.get(str(report_id), {})
    if row.get("pdf_sha256") != digest or row.get("pdf_storage_path") != expected_path:
        return False
    object_url = (
        f"{settings.supabase_url.rstrip('/')}/storage/v1/object/authenticated/"
        f"{quote(settings.pdf_bucket, safe='')}/{quote(expected_path, safe='/')}"
    )
    try:
        response = requests.head(
            object_url,
            headers={"apikey": settings.supabase_secret_key},
            timeout=30,
        )
    except requests.RequestException:
        return False
    return response.ok


class ProgressLog:
    """Write readable live progress and a durable JSONL record for each report."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"ingestion-{stamp}.jsonl"
        self._stream = self.path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        self._stream.close()

    def write(self, message: str, **record: Any) -> None:
        print(message, flush=True)
        self._stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **record}) + "\n")
        self._stream.flush()


def run(args: argparse.Namespace) -> int:
    """Index missing reports, upload missing PDFs, and continue after per-file errors."""
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
    from openai import OpenAI
    import tiktoken

    settings = Settings.from_environment()
    reports = load_reports(args.reports)
    paths = sorted(args.pdf_dir.glob("*.pdf"))
    if not paths:
        raise ValueError(f"No PDFs found in {args.pdf_dir}")

    # Validate the complete local set before writing any remote data.
    identified: list[tuple[Path, UUID, dict[str, Any]]] = []
    seen: set[UUID] = set()
    for path in paths:
        report_id = report_id_from_path(path)
        if report_id in seen:
            raise ValueError(f"Multiple PDFs have report ID {report_id}")
        seen.add(report_id)
        report = reports.get(str(report_id))
        if report is None:
            raise ValueError(f"No scraped metadata for report {report_id} ({path.name})")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"PDF is missing or empty: {path}")
        identified.append((path, report_id, report))

    # Snapshot remote state once; errors here stop before any corpus mutations.
    indexed = fetch_indexed_report_ids(settings)
    pdf_links = fetch_pdf_links(settings)
    encoding = tiktoken.get_encoding("o200k_base")
    converter = make_converter()
    chunker = HybridChunker(
        tokenizer=OpenAITokenizer(tokenizer=encoding, max_tokens=450)
    )
    client = OpenAI()
    progress = ProgressLog(args.log_dir)
    started = time.monotonic()
    failures = 0
    skipped = 0
    indexed_count = 0
    uploaded_count = 0
    try:
        progress.write(
            f"Starting {len(identified)} PDFs; existing indexed reports will be skipped. "
            f"Embedding model: {settings.embedding_model}. Live log: {progress.path}",
            event="start",
            total=len(identified),
            embedding_model=settings.embedding_model,
            log_file=str(progress.path),
        )
        for number, (path, report_id, report) in enumerate(identified, start=1):
            item_started = time.monotonic()
            size_bytes = path.stat().st_size
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                has_index = str(report_id) in indexed
                has_pdf = pdf_is_linked_and_present(report_id, digest, pdf_links, settings)
                actions: list[str] = []

                if has_index:
                    actions.append("index skipped (already indexed)")
                else:
                    chunks = index_pdf(
                        path,
                        report,
                        converter,
                        chunker,
                        lambda text: len(encoding.encode(text)),
                        client,
                        settings,
                        args.batch_size,
                    )
                    indexed_count += 1
                    indexed.add(str(report_id))
                    actions.append(f"indexed {chunks} chunks")

                if has_pdf:
                    actions.append("PDF skipped (hash verified and object exists)")
                else:
                    upload_pdf(path, settings)
                    uploaded_count += 1
                    actions.append("PDF uploaded and verified")

                elapsed = time.monotonic() - item_started
                skipped += int(has_index and has_pdf)
                progress.write(
                    f"[{number}/{len(identified)}] OK {path.name} — {'; '.join(actions)} "
                    f"({size_bytes / 1_000_000:.1f} MB, {elapsed:.1f}s)",
                    event="success",
                    report_id=str(report_id),
                    file=path.name,
                    size_bytes=size_bytes,
                    indexed=not has_index,
                    pdf_uploaded=not has_pdf,
                    elapsed_seconds=round(elapsed, 2),
                    actions=actions,
                )
            except Exception as exc:
                failures += 1
                elapsed = time.monotonic() - item_started
                # Third-party exception bodies may contain request details or secrets.
                progress.write(
                    f"[{number}/{len(identified)}] FAILED {path.name} "
                    f"({type(exc).__name__}, {elapsed:.1f}s); see configuration/network and retry",
                    event="failure",
                    report_id=str(report_id),
                    file=path.name,
                    error_type=type(exc).__name__,
                    elapsed_seconds=round(elapsed, 2),
                )
        elapsed = time.monotonic() - started
        progress.write(
            f"Finished: {len(identified)} PDFs, {indexed_count} indexed, "
            f"{uploaded_count} uploaded, {skipped} fully skipped, {failures} failed "
            f"in {elapsed / 60:.1f} minutes. Log: {progress.path}",
            event="summary",
            total=len(identified),
            indexed=indexed_count,
            uploaded=uploaded_count,
            fully_skipped=skipped,
            failures=failures,
            elapsed_seconds=round(elapsed, 2),
            log_file=str(progress.path),
        )
    finally:
        progress.close()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path, help="Directory containing UUID-named PDFs")
    parser.add_argument(
        "--reports", type=Path, default=Path("data/scraped/landing_pages.jsonl")
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--log-dir", type=Path, default=Path("data/ingestion_runs"))
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    try:
        return run(args)
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        # Avoid printing service exception messages, which can contain request details.
        print(f"Preflight failed ({type(exc).__name__}). No further files were processed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
