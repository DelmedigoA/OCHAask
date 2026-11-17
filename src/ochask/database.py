"""Server-side access to Supabase RPC functions."""

from __future__ import annotations

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
) -> list[dict[str, Any]]:
    """Read all rows from a known PostgREST table, paging past its row limit."""
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")

    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        headers = {
            "apikey": key,
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + page_size - 1}",
        }
        if total is None:
            headers["Prefer"] = "count=exact"
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

        if total is None:
            content_range = response.headers.get("content-range", "")
            try:
                total_text = content_range.rsplit("/", 1)[1]
                total = None if total_text == "*" else int(total_text)
            except (IndexError, ValueError) as exc:
                raise RuntimeError(f"Supabase {table} did not return an exact count") from exc
            if total is None:
                raise RuntimeError(f"Supabase {table} did not return an exact count")

        rows.extend(page)
        if not page:
            break
        offset += len(page)
    return rows
