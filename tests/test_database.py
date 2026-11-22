import logging

import pytest

from ochask import database


class FailedResponse:
    ok = False
    status_code = 500
    content = b""

    def json(self):
        return {"code": "57014", "message": "private query details"}


def test_rpc_failure_logs_safe_code_and_request_id_without_response_body(monkeypatch, caplog):
    monkeypatch.setattr(database.requests, "post", lambda *_args, **_kwargs: FailedResponse())
    token = database.request_id_var.set("request-123")
    try:
        with caplog.at_level(logging.WARNING, logger="ochask.database"):
            with pytest.raises(database.SupabaseRPCError, match="HTTP 500, code 57014"):
                database.rpc("https://example.test", "test-secret", "hybrid_search", {})
    finally:
        database.request_id_var.reset(token)

    assert "operation=hybrid_search" in caplog.text
    assert "error_code=57014" in caplog.text
    assert "request_id=request-123" in caplog.text
    assert "private query details" not in caplog.text
    assert "test-secret" not in caplog.text


def test_rest_failure_logs_safe_code_and_request_id_without_response_body(monkeypatch, caplog):
    monkeypatch.setattr(database.requests, "get", lambda *_args, **_kwargs: FailedResponse())
    token = database.request_id_var.set("request-456")
    try:
        with caplog.at_level(logging.WARNING, logger="ochask.database"):
            with pytest.raises(database.SupabaseRestError, match="HTTP 500, code 57014"):
                database.rest_rows(
                    "https://example.test",
                    "test-secret",
                    "report_embeddings",
                    select="report_id,token_count",
                )
    finally:
        database.request_id_var.reset(token)

    assert "operation=report_embeddings" in caplog.text
    assert "request_id=request-456" in caplog.text
    assert "private query details" not in caplog.text
    assert "test-secret" not in caplog.text


def test_rest_network_failure_uses_safe_typed_error(monkeypatch, caplog):
    def fail(*_args, **_kwargs):
        raise database.requests.ConnectionError("private host and secret")

    monkeypatch.setattr(database.requests, "get", fail)
    with caplog.at_level(logging.WARNING, logger="ochask.database"):
        with pytest.raises(database.SupabaseRestError, match="ConnectionError") as error:
            database.rest_rows(
                "https://example.test",
                "test-secret",
                "report_embeddings",
                select="report_id,token_count",
                request_id="request-network",
            )

    assert error.value.__cause__ is None
    assert "private host and secret" not in str(error.value)
    assert "private host and secret" not in caplog.text
    assert "request_id=request-network" in caplog.text
