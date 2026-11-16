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
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase {function} failed (HTTP {response.status_code})")
    return response.json() if response.content else None
