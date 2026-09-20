from fastapi.testclient import TestClient

from llm_cost_router.api.main import app

client = TestClient(app)


def test_health_and_defaults():
    assert client.get("/health").json() == {"status": "ok", "mode": "live-and-planning"}
    assert len(client.get("/api/v1/defaults").json()["models"]) == 3


def test_compare_roundtrip_and_decimal_serialization():
    payload = client.get("/api/v1/defaults").json()
    payload["requests_per_month"] = 10
    response = client.post("/api/v1/compare", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["routed"]["requested"] == 10
    assert isinstance(result["routed"]["monthly_cost_usd"], str)
    assert result["mode"] == "simulation"


def test_invalid_request_returns_422():
    assert client.post("/api/v1/compare", json={"input_tokens": -5}).status_code == 422


def test_duplicate_models_rejected():
    payload = client.get("/api/v1/defaults").json()
    payload["models"].append(payload["models"][0])
    assert client.post("/api/v1/compare", json=payload).status_code == 422
