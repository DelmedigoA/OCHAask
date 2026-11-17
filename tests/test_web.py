from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from ochask.config import Settings
from ochask.storage import get_pdf, upload_pdf

REPORT = "a18780b0-0e72-57e7-a680-fc32d3212c3d"
SETTINGS = Settings("https://example.com", "sb_secret_test", "model")


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
