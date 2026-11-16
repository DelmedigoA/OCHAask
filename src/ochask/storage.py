"""Private PDF storage for report evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import requests

from .config import Settings
from .utils import report_id_from_path


def upload_pdf(path: Path, settings: Settings) -> str:
    """Upload immutable PDF bytes, then associate their hash and object with a report."""
    report_id = report_id_from_path(path)
    content = path.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"Not a PDF: {path.name}")
    digest = hashlib.sha256(content).hexdigest()
    object_path = f"reports/{report_id}/{digest}.pdf"
    base = settings.supabase_url.rstrip("/")
    headers = {"apikey": settings.supabase_secret_key}
    # Content-addressed uploads never overwrite an existing object.
    response = requests.post(
        f"{base}/storage/v1/object/{settings.pdf_bucket}/{object_path}",
        headers={**headers, "Content-Type": "application/pdf", "x-upsert": "false"},
        data=content,
        timeout=120,
    )
    duplicate = response.status_code == 409
    if response.status_code == 400:
        try:
            duplicate = response.json().get("error") == "Duplicate"
        except ValueError, AttributeError:
            duplicate = False
    if response.status_code not in (200, 201) and not duplicate:
        raise RuntimeError(f"PDF upload failed (HTTP {response.status_code})")
    downloaded = requests.get(
        f"{base}/storage/v1/object/authenticated/{settings.pdf_bucket}/{object_path}",
        headers=headers,
        timeout=120,
    )
    if not downloaded.ok or hashlib.sha256(downloaded.content).hexdigest() != digest:
        raise RuntimeError("Uploaded PDF could not be verified")
    updated = requests.patch(
        f"{base}/rest/v1/report_landings",
        headers={
            **headers,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        params={"id": f"eq.{report_id}", "select": "id"},
        json={"pdf_storage_path": object_path, "pdf_sha256": digest},
        timeout=30,
    )
    if not updated.ok or len(updated.json()) != 1:
        raise RuntimeError(f"Report association failed (HTTP {updated.status_code})")
    return object_path


def get_pdf(report_id: UUID, settings: Settings) -> bytes:
    """Load the stored PDF for a report, verifying its content hash."""
    base = settings.supabase_url.rstrip("/")
    headers = {"apikey": settings.supabase_secret_key}
    response = requests.get(
        f"{base}/rest/v1/report_landings",
        headers=headers,
        params={"id": f"eq.{report_id}", "select": "pdf_storage_path,pdf_sha256"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError("Report lookup failed")
    rows = response.json()
    if not rows or not rows[0].get("pdf_storage_path") or not rows[0].get("pdf_sha256"):
        raise FileNotFoundError("PDF not available for this report")
    digest = rows[0]["pdf_sha256"]
    # Derive the path from a validated UUID and hash rather than trusting arbitrary paths.
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RuntimeError("Invalid stored PDF hash")
    object_path = f"reports/{report_id}/{digest}.pdf"
    if rows[0]["pdf_storage_path"] != object_path:
        raise RuntimeError("Invalid stored PDF path")
    pdf = requests.get(
        f"{base}/storage/v1/object/authenticated/{settings.pdf_bucket}/{object_path}",
        headers=headers,
        timeout=120,
    )
    if pdf.status_code == 404:
        raise FileNotFoundError("PDF not found in storage")
    if not pdf.ok or hashlib.sha256(pdf.content).hexdigest() != digest:
        raise RuntimeError("PDF download or verification failed")
    return pdf.content
