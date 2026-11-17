"""Rate-limited, cached place-name lookup through Nominatim."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import requests

_MIN_REQUEST_INTERVAL = 1.0
_REQUEST_LOCK = threading.Lock()


def _tool_result(query: str, places: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "query": query,
        "places": places,
        "ambiguous": len(places) > 1,
        **({} if places else {"error": "No matching places were found."}),
    }


def _connect(cache_path: str | Path) -> sqlite3.Connection:
    path = Path(cache_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute(
        "CREATE TABLE IF NOT EXISTS nominatim_cache ("
        "query_key TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS nominatim_state ("
        "name TEXT PRIMARY KEY, last_request_at REAL NOT NULL)"
    )
    db.commit()
    return db


def _place_results(payload: Any, query: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Nominatim returned an unexpected response")

    places: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        try:
            lat = float(item["lat"])
            lng = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue

        name = item.get("name")
        display_name = item.get("display_name")
        osm_type = item.get("osm_type")
        osm_id = item.get("osm_id")
        place_id = item.get("place_id")
        stable_id = (
            f"osm-{osm_type}-{osm_id}"
            if osm_type is not None and osm_id is not None
            else f"nominatim-{place_id if place_id is not None else index}"
        )
        places.append(
            {
                "id": stable_id,
                "label": (name.strip()[:160] if isinstance(name, str) and name.strip() else query[:160]),
                "lat": lat,
                "lng": lng,
                **(
                    {"description": display_name.strip()[:320]}
                    if isinstance(display_name, str) and display_name.strip()
                    else {}
                ),
            }
        )
    return places


def search_location(
    query: str, *, city: bool = False, country_code: str | None = None
) -> dict[str, Any]:
    """Find candidate places as assistant-ui GeoMapPlace data.

    Queries and results are cached in the workspace SQLite database. Requests
    are serialized and spaced at least one second apart across local workers.
    """
    normalized = " ".join(query.split())
    if not normalized:
        return {"query": query, "places": [], "error": "Provide a place name to look up."}

    endpoint = os.getenv("OCHASK_NOMINATIM_SEARCH_URL", "").strip() or (
        "https://nominatim.openstreetmap.org/search"
    )
    user_agent = os.getenv(
        "OCHASK_NOMINATIM_USER_AGENT",
        "OCHAsk/0.1 (OCHA humanitarian report research workspace)",
    ).strip() or "OCHAsk/0.1 (OCHA humanitarian report research workspace)"
    cache_path = os.getenv(
        "OCHASK_WORKSPACE_DB_PATH", "data/ochask-workspace.sqlite3"
    ).strip() or "data/ochask-workspace.sqlite3"
    params = {
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
        "accept-language": "en",
    }
    if city:
        params.update({"city": normalized, "featureType": "city"})
    else:
        params["q"] = normalized
    if country_code:
        params["countrycodes"] = country_code.lower()

    cache_key = f"{endpoint}\n{normalized.casefold()}"
    if city or country_code:
        cache_key += f"\n{'city' if city else 'free-form'}:{country_code.lower() if country_code else ''}"

    with _REQUEST_LOCK:
        db = _connect(cache_path)
        try:
            cached = db.execute(
                "SELECT result_json FROM nominatim_cache WHERE query_key=?",
                (cache_key,),
            ).fetchone()
            if cached:
                places = json.loads(cached[0])
                return _tool_result(normalized, places)

            # Persist the throttle timestamp so multiple local workers share it.
            db.execute("BEGIN IMMEDIATE")
            cached = db.execute(
                "SELECT result_json FROM nominatim_cache WHERE query_key=?",
                (cache_key,),
            ).fetchone()
            if cached:
                places = json.loads(cached[0])
                db.commit()
                return _tool_result(normalized, places)

            last = db.execute(
                "SELECT last_request_at FROM nominatim_state WHERE name='public-api'"
            ).fetchone()
            if last:
                wait = _MIN_REQUEST_INTERVAL - (time.time() - float(last[0]))
                if wait > 0:
                    time.sleep(wait)
            db.execute(
                "INSERT INTO nominatim_state(name,last_request_at) VALUES('public-api',?) "
                "ON CONFLICT(name) DO UPDATE SET last_request_at=excluded.last_request_at",
                (time.time(),),
            )
            db.commit()
        finally:
            db.close()

        try:
            response = requests.get(
                endpoint,
                params=params,
                headers={"User-Agent": user_agent},
                timeout=(3, 12),
            )
            response.raise_for_status()
            places = _place_results(response.json(), normalized)
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f" (HTTP {status})" if status is not None else ""
            return {
                "query": normalized,
                "places": [],
                "error": f"Nominatim lookup is temporarily unavailable{detail}.",
            }
        except (ValueError, json.JSONDecodeError):
            return {
                "query": normalized,
                "places": [],
                "error": "Nominatim returned an invalid response.",
            }

        db = _connect(cache_path)
        try:
            db.execute(
                "INSERT OR REPLACE INTO nominatim_cache(query_key,result_json) VALUES(?,?)",
                (cache_key, json.dumps(places, ensure_ascii=False)),
            )
            db.commit()
        finally:
            db.close()
        return _tool_result(normalized, places)
