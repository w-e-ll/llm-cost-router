import io
import json
import logging
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_cost_router.api.main import app
from llm_cost_router.core.live import attempt
from llm_cost_router.core.setup_logger import EventFormatter, request_id, run_id


@pytest.fixture
def captured_events(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(EventFormatter())
    logger = logging.getLogger("llm_cost_router")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield output
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_http_correlation_and_no_url_secrets(captured_events):
    with TestClient(app) as client:
        first = client.get("/health?key=PRIVATE_QUERY", headers={"Authorization": "PRIVATE_HEADER"})
        second = client.get("/PRIVATE_PATH?token=PRIVATE_QUERY")
    entries = [json.loads(line) for line in captured_events.getvalue().splitlines()]
    http = [row for row in entries if row["event"] == "http.request"]
    assert http[0]["request_id"] == first.headers["X-Request-ID"]
    assert http[1]["request_id"] == second.headers["X-Request-ID"]
    assert http[0]["request_id"] != http[1]["request_id"]
    assert http[1]["route"] == "unmatched"
    assert "PRIVATE" not in captured_events.getvalue()
    assert request_id.get() == "-"


def test_provider_failure_logs_metadata_without_body(captured_events):
    def provider(request):
        return httpx.Response(429, text="PRIVATE_PROVIDER_BODY", request=request)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        result = attempt(client, "gpt-4.1-nano", {"id": "task", "prompt": "PRIVATE_PROMPT"})
    assert result["fatal"]
    entry = json.loads(captured_events.getvalue().splitlines()[-1])
    assert entry["event"] == "model.attempt"
    assert entry["failed"] is True
    assert entry["cost_usd"] is None
    assert "PRIVATE" not in captured_events.getvalue()


def test_budget_rejection_has_run_and_request_ids(repository, captured_events):
    identifier = str(uuid4())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/run",
            json={
                "run_id": identifier,
                "confirm_paid": True,
                "budget_usd": "0",
            },
        )
    assert response.status_code == 422
    entries = [json.loads(line) for line in captured_events.getvalue().splitlines()]
    rejection = next(row for row in entries if row["event"] == "experiment.budget_rejected")
    assert rejection["run_id"] == identifier
    assert rejection["request_id"] == response.headers["X-Request-ID"]
    assert run_id.get() == "-"


def test_logger_setup_is_idempotent_and_rotates(monkeypatch, tmp_path):
    from llm_cost_router.core import setup_logger as module

    isolated = logging.getLogger(f"test.logging.{uuid4().hex}")
    original_get_logger = logging.getLogger
    monkeypatch.setattr(
        module.logging,
        "getLogger",
        lambda name=None: isolated if name == "llm_cost_router" else original_get_logger(name),
    )
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "events.log"))
    module.setup_logger()
    module.setup_logger()
    assert len(isolated.handlers) == 2
    file_handler = next(h for h in isolated.handlers if hasattr(h, "maxBytes"))
    file_handler.maxBytes = 100
    try:
        module.event(isolated, "rotation.check", payload="x" * 80)
        file_handler.flush()
        file_handler.doRollover()
        assert (tmp_path / "events.log.1").exists()
        assert file_handler.backupCount == 10
    finally:
        for handler in isolated.handlers:
            handler.close()
