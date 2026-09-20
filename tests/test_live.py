import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_cost_router.api import live
from llm_cost_router.api.main import app
from llm_cost_router.core.live import (
    DATASET,
    LiveConfig,
    LiveRequest,
    estimate,
    execute,
    token_cost,
)


def response_for(request, answer="positive", usage=None):
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "model": json.loads(request.content)["model"],
            "usage": usage
            or {
                "input_tokens": 100,
                "output_tokens": 10,
                "input_tokens_details": {"cached_tokens": 20},
            },
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps({"answer": answer})}],
                }
            ],
        },
    )


def test_cached_token_pricing():
    assert token_cost("gpt-4.1-mini", 100, 10, 20) == Decimal("0.00005")


def test_budget_preflight_and_no_calls_when_rejected():
    assert estimate(LiveConfig())["within_budget"]
    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("Paid call"))) as client,
        pytest.raises(ValueError),
    ):
        execute(LiveConfig(budget_usd=0), client)


def test_live_correctness_and_separate_calls():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        task = next(t for t in DATASET if t["prompt"] == payload["input"])
        assert "expected" not in payload
        assert payload["max_output_tokens"] == 128
        assert payload["store"] is False
        return response_for(request, task["expected"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute(LiveConfig(task_count=2), client)
    assert len(requests) == 4
    assert result["status"] == "completed"
    assert result["summaries"]["routed"]["correct"] == 2
    assert Decimal(result["savings_usd"]) > 0


def test_wrong_answer_does_not_use_evaluation_oracle_for_fallback():
    with httpx.Client(transport=httpx.MockTransport(lambda r: response_for(r, "WRONG"))) as client:
        result = execute(LiveConfig(task_count=2), client)
    assert result["summaries"]["routed"]["correct"] == 0
    assert result["summaries"]["routed"]["fallback_calls"] == 0


def test_invalid_answer_fallback_costs_both_calls():
    def handler(request):
        response = response_for(request)
        data = response.json()
        if json.loads(request.content)["model"] == "gpt-4.1-nano":
            data["status"] = "incomplete"
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute(LiveConfig(task_count=2), client)
    assert result["summaries"]["routed"]["fallback_calls"] == 1
    assert result["summaries"]["routed"]["input_tokens"] == 300


@pytest.mark.parametrize("code", [401, 403, 429, 500])
def test_api_errors_stop_without_exposing_response_body(code):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(code, text="secret-provider-details")
        )
    ) as client:
        result = execute(LiveConfig(task_count=2), client)
    assert result["status"] == "aborted"
    assert len(result["rows"]) == 1
    assert result["savings_usd"] is None
    assert "secret-provider-details" not in json.dumps(result)
    assert result["summaries"]["baseline"]["unknown_usage_calls"] == 1


def test_timeout_stops_and_preserves_unknown_usage():
    def handler(request):
        raise httpx.ReadTimeout("secret")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute(LiveConfig(), client)
    assert result["status"] == "aborted"
    assert len(result["rows"]) == 1
    assert result["summaries"]["baseline"]["unknown_usage_reserve_usd"] != "0"


def test_missing_usage_stops():
    def handler(request):
        data = response_for(request).json()
        data.pop("usage")
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute(LiveConfig(), client)
    assert result["status"] == "aborted"
    assert result["savings_usd"] is None


def test_missing_key_and_confirmation(monkeypatch, repository):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    request = LiveRequest(run_id=uuid4(), confirm_paid=True).model_dump(mode="json")
    assert client.get("/api/v1/live/config").json()["key_configured"] is False
    assert client.post("/api/v1/live/run", json=request).status_code == 503
    request["confirm_paid"] = False
    assert client.post("/api/v1/live/run", json=request).status_code == 422


def test_idempotent_saved_run_and_conflict(monkeypatch, repository):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    count = []

    def fake_execute(request, client):
        count.append(1)
        return {"status": "completed", "config": request.model_dump(mode="json")}

    monkeypatch.setattr(live, "execute", fake_execute)
    client = TestClient(app)
    request = LiveRequest(run_id=uuid4(), confirm_paid=True).model_dump(mode="json")
    first = client.post("/api/v1/live/run", json=request)
    second = client.post("/api/v1/live/run", json=request)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(count) == 1
    assert "test-not-real" not in json.dumps(repository.get_run(request["run_id"]))
    request["task_count"] = 2
    assert client.post("/api/v1/live/run", json=request).status_code == 409
