import json

from starlette.testclient import TestClient

from ochask.config import Settings
from ochask.web.app import app


def test_request_id_is_returned_and_correlated_with_access_log(monkeypatch, capsys):
    monkeypatch.setattr(
        "ochask.web.app.Settings.from_environment",
        lambda: Settings("https://example.test", "secret", "model"),
    )
    monkeypatch.setattr(
        "ochask.web.app.corpus_overview",
        lambda _settings: {"reports_count": 0, "reports": []},
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/corpus")

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    log = json.loads(capsys.readouterr().out.strip())
    assert log["request_id"] == request_id
    assert log["endpoint"] == "/api/corpus"
    assert log["status"] == 200
