"""Server-side access to Supabase RPC functions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests


def rpc(url: str, key: str, function: str, payload: dict[str, Any]) -> Any:
    """Call a Supabase RPC without exposing credentials or error response bodies."""
    response = requests.post(
        f"{url.rstrip('/')}/rest/v1/rpc/{function}",
        headers={
            "apikey": key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase {function} failed (HTTP {response.status_code})")
    return response.json() if response.content else None


def count_indexed_reports(url: str, key: str) -> int:
    """Count indexed report records without returning report data to the client."""
    response = requests.get(
        f"{url.rstrip('/')}/rest/v1/report_landings",
        headers={
            "apikey": key,
            "Prefer": "count=exact",
            "Range": "0-0",
        },
        params={"select": "id"},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(
            f"Supabase report count failed (HTTP {response.status_code})"
        )
    content_range = response.headers.get("content-range", "")
    try:
        return int(content_range.rsplit("/", 1)[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("Supabase did not return an exact report count") from exc


def indexed_report_stats(url: str, key: str) -> tuple[int, str | None, str | None]:
    """Return the indexed report count and earliest/latest OCHA posted dates."""
    endpoint = f"{url.rstrip('/')}/rest/v1/report_landings"
    headers = {
        "apikey": key,
        "Prefer": "count=exact",
        "Range": "0-0",
    }
    try:
        earliest_response = requests.get(
            endpoint,
            headers=headers,
            params={
                "select": "posted",
                "order": "posted.asc.nullslast",
                "limit": "1",
            },
            timeout=15,
        )
        latest_response = requests.get(
            endpoint,
            headers={"apikey": key, "Range": "0-0"},
            params={
                "select": "posted",
                "order": "posted.desc.nullslast",
                "limit": "1",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Supabase report statistics request failed") from exc

    if not earliest_response.ok:
        raise RuntimeError(
            f"Supabase report statistics failed (HTTP {earliest_response.status_code})"
        )
    if not latest_response.ok:
        raise RuntimeError(
            f"Supabase report dates failed (HTTP {latest_response.status_code})"
        )
    content_range = earliest_response.headers.get("content-range", "")
    try:
        count = int(content_range.rsplit("/", 1)[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("Supabase did not return an exact report count") from exc

    earliest_rows = earliest_response.json()
    latest_rows = latest_response.json()
    earliest = earliest_rows[0].get("posted") if earliest_rows else None
    latest = latest_rows[0].get("posted") if latest_rows else None
    return count, earliest, latest


def rest_rows(
    url: str,
    key: str,
    table: str,
    *,
    select: str,
    filters: dict[str, str] | None = None,
    page_size: int = 1000,
    count_mode: str = "exact",
) -> list[dict[str, Any]]:
    """Read all rows from a known PostgREST table, paging past its row limit."""
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    if count_mode not in {"exact", "planned", "estimated"}:
        raise ValueError("count_mode must be exact, planned, or estimated")

    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"

    def fetch_page(
        offset: int,
        range_size: int = page_size,
        *,
        include_count: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        headers = {
            "apikey": key,
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + range_size - 1}",
        }
        if include_count:
            headers["Prefer"] = f"count={count_mode}"
        try:
            response = requests.get(
                endpoint,
                headers=headers,
                params={"select": select, **(filters or {})},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Supabase {table} request failed") from exc
        if not response.ok:
            raise RuntimeError(f"Supabase {table} request failed (HTTP {response.status_code})")
        try:
            page = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Supabase {table} returned invalid JSON") from exc
        if not isinstance(page, list):
            raise RuntimeError(f"Supabase {table} returned an unexpected response")

        total = None
        if include_count:
            content_range = response.headers.get("content-range", "")
            try:
                total_text = content_range.rsplit("/", 1)[1]
                total = None if total_text == "*" else int(total_text)
            except (IndexError, ValueError) as exc:
                raise RuntimeError(
                    f"Supabase {table} did not return a {count_mode} count"
                ) from exc
            if total is None:
                raise RuntimeError(f"Supabase {table} did not return a {count_mode} count")
        return page, total

    first_page, total = fetch_page(0, include_count=True)
    if not first_page and total == 0:
        return []
    if not first_page and total:
        raise RuntimeError(
            f"Supabase {table} returned an empty page before its reported count"
        )
    effective_page_size = len(first_page)
    offsets = range(effective_page_size, total, effective_page_size)
    # PostgREST caps page sizes at 1,000. Fetch the remaining pages concurrently
    # instead of serially paying a cross-region round trip for every page.
    last_page: list[dict[str, Any]] = first_page
    last_offset = 0
    rows = first_page.copy()
    if offsets:
        with ThreadPoolExecutor(max_workers=min(8, len(offsets))) as executor:
            remaining_pages = executor.map(
                lambda offset: fetch_page(offset, effective_page_size), offsets
            )
        for offset, (page, _) in zip(offsets, remaining_pages):
            rows.extend(page)
            last_page = page
            last_offset = offset

    # Planned counts avoid an exact COUNT scan for large filtered tables. If
    # the estimate undershoots, keep paging while the final page was full.
    while len(last_page) == effective_page_size:
        page, _ = fetch_page(last_offset + effective_page_size, effective_page_size)
        if not page:
            break
        rows.extend(page)
        last_page = page
        last_offset += effective_page_size
    return rows
