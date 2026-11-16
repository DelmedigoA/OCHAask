from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.testclient import TestClient

from ochask.chat import SearchContext
from ochask.config import Settings
from ochask.storage import get_pdf, upload_pdf
from ochask.web.app import app, validated_citations

REPORT = "a18780b0-0e72-57e7-a680-fc32d3212c3d"
SETTINGS = Settings("https://example.com", "sb_secret_test", "model")


def test_citations_must_match_source_and_page():
    context = SearchContext(
        sources={
            "S1": {
                "report_id": REPORT,
                "chunk_id": "c",
                "pages": [2, 3],
                "title": "Report",
                "landing_url": "https://example.com",
                "content": "water",
                "fusion_score": 1,
            }
        }
    )
    result = validated_citations(
        "[[S1:p2|water]] [[S1:p4|wrong page]] [[S2:p2|unknown]]", context
    )
    assert len(result) == 1
    assert result[0]["page"] == 2
    assert result[0]["report_id"] == REPORT


def test_pdf_route_missing_and_failed(monkeypatch):
    from ochask.web import app as module

    monkeypatch.setattr(module.Settings, "from_environment", lambda: SETTINGS)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/reports/not-a-uuid/pdf").status_code == 404

        def missing(*args):
            raise FileNotFoundError

        monkeypatch.setattr(module, "get_pdf", missing)
        assert client.get(f"/api/reports/{REPORT}/pdf").status_code == 404

        def failure(*args):
            raise RuntimeError

        monkeypatch.setattr(module, "get_pdf", failure)
        assert client.get(f"/api/reports/{REPORT}/pdf").status_code == 502


def test_storage_failure_does_not_associate(monkeypatch, tmp_path: Path):
    from ochask import storage

    path = tmp_path / f"{REPORT}_report.pdf"
    path.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        storage.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=500),
    )
    monkeypatch.setattr(
        storage.requests,
        "patch",
        lambda *args, **kwargs: pytest.fail("must not associate"),
    )
    with pytest.raises(RuntimeError, match="upload failed"):
        upload_pdf(path, SETTINGS)


def test_storage_download_rejects_missing_file(monkeypatch):
    from ochask import storage

    monkeypatch.setattr(
        storage.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(ok=True, json=lambda: []),
    )
    with pytest.raises(FileNotFoundError):
        get_pdf(UUID(REPORT), SETTINGS)


def test_duplicate_upload_is_verified_before_association(monkeypatch, tmp_path):
    from ochask import storage

    path = tmp_path / f"{REPORT}_report.pdf"
    data = b"%PDF-test"
    path.write_bytes(data)
    monkeypatch.setattr(
        storage.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=400, json=lambda: {"error": "Duplicate"}
        ),
    )
    monkeypatch.setattr(
        storage.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(ok=True, content=data),
    )
    calls = []

    def patch(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(ok=True, status_code=200, json=lambda: [{"id": REPORT}])

    monkeypatch.setattr(storage.requests, "patch", patch)
    assert upload_pdf(path, SETTINGS).startswith(f"reports/{REPORT}/")
    assert len(calls) == 1
