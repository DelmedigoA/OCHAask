"""Read-only corpus summaries and presentation metadata for workspace pages."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .config import Settings
from .database import rest_rows

if TYPE_CHECKING:
    from .search import SearchResult


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _publication_date(report: dict[str, Any]) -> date | None:
    return _date(report.get("originally_published")) or _date(report.get("posted"))


def _all_months(
    counts: Counter[str], earliest: date | None, latest: date | None
) -> list[dict[str, Any]]:
    if earliest is None or latest is None:
        return []
    first_month = earliest.year * 12 + earliest.month - 1
    last_month = latest.year * 12 + latest.month - 1
    result: list[dict[str, Any]] = []
    for serial in range(first_month, last_month + 1):
        month_year, month_zero = divmod(serial, 12)
        month_number = month_zero + 1
        key = f"{month_year:04d}-{month_number:02d}"
        label = date(month_year, month_number, 1).strftime("%b %y")
        result.append({"month": key, "label": label, "reports": counts[key]})
    return result


def corpus_overview(settings: Settings) -> dict[str, Any]:
    """Aggregate indexed chunks without treating chunk tokens as unique document tokens."""
    with ThreadPoolExecutor(max_workers=3) as executor:
        chunks_future = executor.submit(
            rest_rows,
            settings.supabase_url,
            settings.supabase_secret_key,
            "report_embeddings",
            select="report_id,token_count",
            filters={
                "embedding_model": f"eq.{settings.embedding_model}",
                "chunk_id": "not.is.null",
            },
            count_mode="planned",
        )
        reports_future = executor.submit(
            rest_rows,
            settings.supabase_url,
            settings.supabase_secret_key,
            "report_landings",
            select="id,title,posted,originally_published,landing_url,origin_url",
        )
        attachments_future = executor.submit(
            rest_rows,
            settings.supabase_url,
            settings.supabase_secret_key,
            "report_attachments",
            select="report_id,filename,url,file_type,language",
        )
        chunks = chunks_future.result()
        report_rows = reports_future.result()
        attachments = attachments_future.result()

    per_report: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"chunk_tokens": 0, "token_count_complete": True}
    )
    for chunk in chunks:
        report_id = chunk.get("report_id")
        if not isinstance(report_id, str):
            continue
        summary = per_report[report_id]
        token_count = chunk.get("token_count")
        if isinstance(token_count, int) and token_count >= 0:
            summary["chunk_tokens"] += token_count
        else:
            summary["token_count_complete"] = False
    report_ids = set(per_report)
    if not report_ids:
        return {
            "reports_count": 0,
            "chunk_token_count": 0,
            "chunk_token_count_complete": True,
            "date_start": None,
            "date_end": None,
            "monthly": [],
            "reports": [],
        }

    reports_by_id = {
        row["id"]: row
        for row in report_rows
        if isinstance(row.get("id"), str) and row["id"] in report_ids
    }
    missing_report_ids = report_ids - reports_by_id.keys()
    if missing_report_ids:
        raise RuntimeError("Indexed chunks are missing report metadata")

    pdfs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attachment in attachments:
        report_id = attachment.get("report_id")
        if report_id not in report_ids:
            continue
        filename = str(attachment.get("filename", ""))
        file_type = str(attachment.get("file_type", ""))
        if filename.lower().endswith(".pdf") or "pdf" in file_type.lower():
            attachment["url"] = _safe_http_url(attachment.get("url"))
            if attachment["url"]:
                pdfs[report_id].append(attachment)

    results: list[dict[str, Any]] = []
    month_counts: Counter[str] = Counter()
    known_dates: list[date] = []
    known_tokens = 0
    all_token_counts_complete = True
    for report_id in report_ids:
        report = reports_by_id[report_id]
        summary = per_report[report_id]
        published = _publication_date(report)
        if published:
            known_dates.append(published)
            month_counts[published.strftime("%Y-%m")] += 1

        candidates = pdfs.get(report_id, [])
        candidates.sort(
            key=lambda item: (
                str(item.get("language", "")).casefold() not in {"english", "en"},
                str(item.get("filename", "")).casefold(),
            )
        )
        pdf_url = candidates[0]["url"] if candidates else None
        report_url = _safe_http_url(report.get("origin_url")) or _safe_http_url(
            report.get("landing_url")
        )
        source_url = pdf_url or report_url
        token_count_complete = bool(summary["token_count_complete"])
        known_tokens += summary["chunk_tokens"]
        all_token_counts_complete = all_token_counts_complete and token_count_complete

        results.append(
            {
                "id": report_id,
                "title": report.get("title"),
                "publication_date": published.isoformat() if published else None,
                "chunk_token_count": summary["chunk_tokens"],
                "token_count_complete": token_count_complete,
                "landing_url": _safe_http_url(report.get("landing_url")),
                "source_url": source_url,
            }
        )

    latest = max(known_dates) if known_dates else None
    earliest = min(known_dates) if known_dates else None
    return {
        "reports_count": len(report_ids),
        "chunk_token_count": known_tokens,
        "chunk_token_count_complete": all_token_counts_complete,
        "date_start": earliest.isoformat() if earliest else None,
        "date_end": latest.isoformat() if latest else None,
        "monthly": _all_months(month_counts, earliest, latest),
        "reports": results,
    }


def search_evidence(query: str, limit: int, settings: Settings) -> list[dict[str, Any]]:
    """Use the agent's Search Reports retrieval helper and preserve its result order."""
    from .chat import search_reports

    results: list[SearchResult] = search_reports(query, limit=limit)
    report_ids = list(dict.fromkeys(result["report_id"] for result in results))
    if not report_ids:
        return []

    date_rows = rest_rows(
        settings.supabase_url,
        settings.supabase_secret_key,
        "report_landings",
        select="id,posted,originally_published",
        filters={"id": f"in.({','.join(report_ids)})"},
    )
    dates = {
        row["id"]: _publication_date(row).isoformat()
        if _publication_date(row)
        else None
        for row in date_rows
        if isinstance(row.get("id"), str)
    }
    return [
        {
            **result,
            "publication_date": dates.get(result["report_id"]),
        }
        for result in results
    ]
