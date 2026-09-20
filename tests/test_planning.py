from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from llm_cost_router.api import live
from llm_cost_router.api.main import app
from llm_cost_router.core.planning import PlanningRequest, project_run


@pytest.fixture
def saved():
    rows = []
    for policy, cost in [("baseline", "0.001"), ("routed", "0.0005")]:
        for i in range(2):
            rows.append(
                {
                    "id": str(i),
                    "policy": policy,
                    "complexity": "simple" if i == 0 else "complex",
                    "correct": i == 0,
                    "error": None,
                    "latency_ms": 100 + i * 100,
                    "attempts": [
                        {
                            "model": "gpt-4.1-mini",
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cached_tokens": 20,
                            "cost_usd": cost,
                        }
                    ],
                }
            )
    return {
        "mode": "live",
        "status": "completed",
        "run_id": str(uuid4()),
        "created_at": "2026-09-17T00:00:00Z",
        "dataset_version": "test-v1",
        "config": {"task_count": 2},
        "prices": {"saved-rate": "unchanged"},
        "price_date": "2026-09-17",
        "rows": rows,
    }


def test_projection_and_budget(saved):
    original = deepcopy(saved)
    result = project_run(saved, PlanningRequest(run_id=saved["run_id"], monthly_budget_usd=75))
    baseline, routed = result["policies"]
    assert Decimal(baseline["monthly_cost_usd"]) == 100
    assert Decimal(routed["monthly_cost_usd"]) == 50
    assert Decimal(result["projected_savings_usd"]) == 50
    assert baseline["estimated_requests_affordable"] == 75000
    assert baseline["monthly_budget_gap_usd"] == "25.000"
    assert routed["within_budget"]
    assert result["source_prices"] == saved["prices"]
    assert baseline["sample_accuracy_percent"] == 50
    assert baseline["sample_p95_ms"] == 200
    assert result["simple_fraction"] == 0.5
    assert saved == original


def test_fallback_included(saved):
    saved["rows"][2]["attempts"].append(deepcopy(saved["rows"][2]["attempts"][0]))
    result = project_run(saved, PlanningRequest(run_id=saved["run_id"]))
    assert Decimal(result["policies"][1]["monthly_cost_usd"]) == 75
    assert result["policies"][1]["avg_input_tokens"] == 150
    assert result["policies"][1]["sample_fallbacks"] == 1


def test_zero_volume_and_zero_cost(saved):
    result = project_run(saved, PlanningRequest(run_id=saved["run_id"], monthly_requests=0))
    assert Decimal(result["projected_savings_usd"]) == 0
    assert result["projected_savings_percent"] is None
    for row in saved["rows"]:
        row["attempts"][0]["cost_usd"] = "0"
    result = project_run(saved, PlanningRequest(run_id=saved["run_id"]))
    assert result["policies"][0]["estimated_requests_affordable"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "aborted",
        "missing_row",
        "unknown_cost",
        "unknown_tokens",
        "duplicate",
        "mismatched",
        "negative",
        "cached",
    ],
)
def test_bad_source_rejected(saved, fault):
    if fault == "aborted":
        saved["status"] = "aborted"
    elif fault == "missing_row":
        saved["rows"].pop()
    elif fault == "duplicate":
        saved["rows"][1]["id"] = "0"
    elif fault == "mismatched":
        saved["rows"][3]["id"] = "other"
    else:
        attempt = saved["rows"][0]["attempts"][0]
        field, value = {
            "unknown_cost": ("cost_usd", None),
            "unknown_tokens": ("input_tokens", None),
            "negative": ("cost_usd", "-1"),
            "cached": ("cached_tokens", 200),
        }[fault]
        attempt[field] = value
    with pytest.raises(ValueError):
        project_run(saved, PlanningRequest(run_id=saved["run_id"]))


def test_api_reads_by_id_without_key_or_provider_calls(saved, repository, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(live, "execute", lambda *a: pytest.fail("Planning must not call provider"))
    repository.import_run(saved["run_id"], saved)
    client = TestClient(app)
    response = client.post("/api/v1/planning", json={"run_id": saved["run_id"]})
    assert response.status_code == 200
    assert response.json()["source_run_id"] == saved["run_id"]
    assert client.post("/api/v1/planning", json={"run_id": str(uuid4())}).status_code == 404
    assert client.post("/api/v1/planning", json={"run_id": "../secret"}).status_code == 422
    plan_id = response.json()["plan_id"]
    assert client.get(f"/api/v1/planning/{plan_id}").json() == response.json()
    started_id = uuid4()
    repository.claim_run(started_id, {})
    assert client.post("/api/v1/planning", json={"run_id": str(started_id)}).status_code == 409
