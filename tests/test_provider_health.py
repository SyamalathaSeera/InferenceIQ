from fastapi.testclient import TestClient

from app.main import app
from app.providers.base import ProviderError
from app.providers.mock import MockProvider

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


class SelectiveFailingProvider(MockProvider):
    async def complete(self, request):
        if request.model == "mock/quality":
            raise ProviderError(
                "quality model unavailable",
                status_code=503,
                category="unavailable",
                retryable=True,
            )
        return await super().complete(request)


def _completion(client: TestClient, model: str, policy: str | None = None):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Health check"}],
    }
    if policy is not None:
        body["inferenceiq"] = {"policy": policy}
    return client.post("/v1/chat/completions", json=body)


def test_successful_attempt_creates_observed_health_metrics() -> None:
    with TestClient(app) as client:
        completed = _completion(client, "mock/fast")
        health = client.get("/v1/provider-health", headers=ADMIN_HEADERS)

    assert completed.status_code == 200
    record = next(item for item in health.json() if item["model_id"] == "mock/fast")
    assert record["circuit_state"] == "closed"
    assert record["total_successes"] == 1
    assert record["health_score"] == 1
    assert record["latency_ewma_ms"] is not None


def test_repeated_failures_open_circuit_and_auto_routing_avoids_model() -> None:
    with TestClient(app) as client:
        app.state.providers["mock"] = SelectiveFailingProvider()
        failures = [_completion(client, "mock/quality") for _ in range(3)]
        blocked = _completion(client, "mock/quality")
        app.state.providers["mock"] = MockProvider()
        automatic = _completion(client, "auto", policy="quality")
        health = client.get("/v1/provider-health", headers=ADMIN_HEADERS)

    assert all(response.status_code == 503 for response in failures)
    assert blocked.status_code == 503
    assert "Circuit is open" in blocked.json()["detail"]
    receipt = automatic.json()["inference_receipt"]
    assert receipt["selected_model"] != "mock/quality"
    rejected = next(
        item for item in receipt["candidates"] if item["model"] == "mock/quality"
    )
    assert rejected["rejected_reason"] == "circuit_open"
    record = next(item for item in health.json() if item["model_id"] == "mock/quality")
    assert record["circuit_state"] == "open"
    assert record["consecutive_failures"] == 3
    assert record["health_score"] < 1


def test_admin_can_reset_open_circuit() -> None:
    with TestClient(app) as client:
        app.state.providers["mock"] = SelectiveFailingProvider()
        for _ in range(3):
            _completion(client, "mock/quality")
        reset = client.post(
            "/v1/provider-health/reset",
            headers=ADMIN_HEADERS,
            json={"model_id": "mock/quality"},
        )
        app.state.providers["mock"] = MockProvider()
        recovered = _completion(client, "mock/quality")

    assert reset.status_code == 200
    assert reset.json()["circuit_state"] == "closed"
    assert recovered.status_code == 200
