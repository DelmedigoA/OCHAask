"""Rate-limited, cached place-name lookup through Nominatim."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any

import requests
from pydantic import Field, StringConstraints

_MIN_REQUEST_INTERVAL = 1.0
_DEFAULT_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_USER_AGENT = "OCHAsk/0.1 (OCHA humanitarian report research workspace)"
_PlaceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


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


def _route_results(payload: list, query: str) -> list[dict[str, Any]]:
    places = _place_results(payload[:1], query)
    if not places:
        return []
    geometry = payload[0].get("geojson")
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get("coordinates")
    kind = geometry.get("type")
    if kind == "LineString":
        coordinates = [coordinates]
    elif kind != "MultiLineString" or not isinstance(coordinates, list):
        return []

    routes = []
    for index, segment in enumerate(coordinates):
        if not isinstance(segment, list) or len(segment) < 2:
            continue
        if not all(
            isinstance(point, list) and len(point) >= 2
            and type(point[0]) in (int, float) and -180 <= point[0] <= 180
            and type(point[1]) in (int, float) and -90 <= point[1] <= 90
            for point in segment
        ):
            continue
        routes.append({
            "id": f"{places[0]['id']}-{index}",
            "label": places[0]["label"],
            "points": [[point[1], point[0]] for point in segment],
        })
    return routes


def search_location(
    query: str, *, city: bool = False, country_code: str | None = None
) -> dict[str, Any]:
    """Find candidate places as assistant-ui GeoMapPlace data.

    Queries and results are cached in the shared workspace SQLite database.
    """
    normalized = " ".join(query.split())
    if not normalized:
        return {"query": query, "places": [], "error": "Provide a place name to look up."}
    places, error = _lookup(normalized, city=city, country_code=country_code)
    if error:
        return {"query": normalized, "places": [], "error": error}
    return _tool_result(normalized, places)


def mapping(
    locations: Annotated[list[_PlaceName], Field(max_length=5)] | None = None,
    route_names: Annotated[list[_PlaceName], Field(max_length=3)] | None = None,
) -> dict[str, Any]:
    """Map a short list of public places or existing named routes on request.

    Pass names only. Exclude report prose, personal data, and confidential details.
    Avoid autocomplete and bulk lookups.

    Args:
        locations: For a city, send its base name and governorate or district, even
            if they share a name. Drop generic suffixes like City and broad regions.
            Omit the country unless needed for disambiguation. If needed, use
            Nominatim's exact country label. Prefer mapped or local spellings.
        route_names: Existing named roads or paths, qualified by region. The tool
            draws mapped geometry and cannot calculate directions.
    """
    places = {}
    routes = {}
    unresolved = []
    for names, destination, is_route in (
        (locations, places, False), (route_names, routes, True),
    ):
        queries = (" ".join(name.split()) for name in names or ())
        unique_queries = {query.casefold(): query for query in queries if query}
        for query in unique_queries.values():
            matches, error = _lookup(query, route=is_route)
            if matches:
                for match in matches if is_route else matches[:1]:
                    destination.setdefault(match["id"], match)
            else:
                unresolved.append({
                    "query": query,
                    "reason": error or (
                        "No mapped line geometry found." if is_route else "No matching place found."
                    ),
                })
    return {"places": list(places.values()), "routes": list(routes.values()), "unresolved": unresolved}


def _lookup(
    query: str, *, city: bool = False, country_code: str | None = None, route: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    endpoint = os.getenv("OCHASK_NOMINATIM_SEARCH_URL", "").strip() or _DEFAULT_SEARCH_URL
    user_agent = os.getenv("OCHASK_NOMINATIM_USER_AGENT", "").strip() or _DEFAULT_USER_AGENT
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
        params.update({"city": query, "featureType": "city"})
    else:
        params["q"] = query
    if country_code:
        params["countrycodes"] = country_code.lower()

    cache_key = f"{endpoint}\n{query.casefold()}"
    if city or country_code:
        cache_key += f"\n{'city' if city else 'free-form'}:{country_code.lower() if country_code else ''}"
    if route:
        params.update({"polygon_geojson": 1, "limit": 1})
        cache_key += "\nroute"

    with closing(_connect(cache_path)) as db, db:
        # Serialize misses and cache writes across all local workers, including
        # simultaneous requests for the same name. Hits skip the rate limit.
        read_cache = "SELECT result_json FROM nominatim_cache WHERE query_key=?"
        cached = db.execute(read_cache, (cache_key,)).fetchone()
        if cached is not None:
            return json.loads(cached[0]), None
        db.execute("BEGIN IMMEDIATE")
        cached = db.execute(read_cache, (cache_key,)).fetchone()
        if cached is not None:
            return json.loads(cached[0]), None

        last = db.execute(
            "SELECT last_request_at FROM nominatim_state WHERE name='public-api'"
        ).fetchone()
        if last:
            wait = _MIN_REQUEST_INTERVAL - (time.time() - float(last[0]))
            if wait > 0:
                time.sleep(wait)
        try:
            response = requests.get(
                endpoint,
                params=params,
                headers={"User-Agent": user_agent},
                timeout=(3, 12),
                allow_redirects=False,
            )
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                return [], "Nominatim search URL returned a redirect."
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Nominatim returned an unexpected response")
            matches = _route_results(payload, query) if route else _place_results(payload, query)
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f" (HTTP {status})" if status is not None else ""
            return [], f"Nominatim lookup is temporarily unavailable{detail}."
        except ValueError:
            return [], "Nominatim returned an invalid response."
        finally:
            db.execute(
                "INSERT INTO nominatim_state(name,last_request_at) VALUES('public-api',?) "
                "ON CONFLICT(name) DO UPDATE SET last_request_at=excluded.last_request_at",
                (time.time(),),
            )

        db.execute(
            "INSERT INTO nominatim_cache(query_key,result_json) VALUES(?,?)",
            (cache_key, json.dumps(matches, ensure_ascii=False)),
        )
        return matches, None
