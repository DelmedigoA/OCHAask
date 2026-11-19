import json
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

from requests import Response

from ochask import geocoding


def nominatim_result(name, osm_id=1, **fields):
    return {"name": name, "lat": "51.5", "lon": "-0.12", "osm_type": "way", "osm_id": osm_id, **fields}


def response(items):
    result = Response()
    result.status_code = 200
    result._content = json.dumps(items).encode()
    return result


def test_mapping_uses_only_geocoded_points_and_route_lines(monkeypatch, tmp_path):
    monkeypatch.setenv("OCHASK_WORKSPACE_DB_PATH", str(tmp_path / "cache.sqlite3"))
    monkeypatch.setenv("OCHASK_NOMINATIM_USER_AGENT", "Bearing Witness Research Assistant Test")
    monkeypatch.setattr(geocoding, "_MIN_REQUEST_INTERVAL", 0)
    calls = []

    def get(_url, *, params, headers, allow_redirects, **kwargs):
        calls.append(params["q"])
        assert headers["User-Agent"] == "Bearing Witness Research Assistant Test"
        assert allow_redirects is False
        if params["q"] == "Bike trail":
            assert params["polygon_geojson"] == 1
            return response([nominatim_result("Bike trail", 3, geojson={
                "type": "MultiLineString",
                "coordinates": [
                    [[-0.12, 51.5], [-0.10, 51.51]],
                    [[-0.08, 51.55], [-0.07, 51.56]],
                    [[-0.06, 51.58], [-0.05, 999], [-0.04, 51.60]],
                ],
            })])
        if params["q"] == "Park boundary":
            return response([nominatim_result("Park", 4, geojson={"type": "Polygon"})])
        return response([nominatim_result(params["q"], len(calls))])

    monkeypatch.setattr(geocoding.requests, "get", get)
    result = geocoding.mapping(["First", " first ", "Second"], ["Bike trail", "Park boundary"])
    assert calls == ["first", "Second", "Bike trail", "Park boundary"]
    assert [(place["lat"], place["lng"]) for place in result["places"]] == [(51.5, -0.12)] * 2
    assert [route["points"] for route in result["routes"]] == [
        [[51.5, -0.12], [51.51, -0.10]],
        [[51.55, -0.08], [51.56, -0.07]],
    ]
    assert result["unresolved"] == [{"query": "Park boundary", "reason": "No mapped line geometry found."}]
    assert geocoding.mapping(route_names=["BIKE TRAIL"])["routes"] == result["routes"]
    assert len(calls) == 4


def test_shared_cache_and_throttle_serialize_concurrent_lookups(monkeypatch, tmp_path):
    monkeypatch.setenv("OCHASK_WORKSPACE_DB_PATH", str(tmp_path / "cache.sqlite3"))
    starts = []

    def get(_url, *, params, **kwargs):
        starts.append(monotonic())
        return response([nominatim_result(params["q"])])

    monkeypatch.setattr(geocoding.requests, "get", get)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(geocoding.search_location, ["First", " first ", "Second"]))
    assert all(result["places"] for result in results)
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 1
