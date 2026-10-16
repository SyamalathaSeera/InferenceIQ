from fastapi.testclient import TestClient

from app.main import app
from app.providers.base import ProviderError
from app.providers.mock import MockProvider

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


class FailingMockProvider(MockProvider):
    async def complete(self, request):
        raise ProviderError(
            "simulated canary provider failure",
            status_code=503,
            category="unavailable",
            retryable=True,
        )


def _activate_canary(
    client: TestClient,
    *,
    guardrail_min_requests: int = 20,
    max_error_rate: float = 0.1,
) -> dict:
    history = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "Create rollout history"}],
            "inferenceiq": {"policy": "cost"},
        },
    )
    assert history.status_code == 200
    proposal = client.post(
        "/v1/policy-workflows",
        headers=ADMIN_HEADERS,
        json={
            "name": "live-quality-policy",
            "policy": "quality",
            "canary_percent": 100,
            "guardrail_min_requests": guardrail_min_requests,
            "max_error_rate": max_error_rate,
        },
    )
    assert proposal.status_code == 201
    policy = proposal.json()
    review = client.post(
        f"/v1/policy-workflows/{policy['id']}/review",
        headers=ADMIN_HEADERS,
        json={"approved": True, "reviewed_by": "release-manager"},
    )
    assert review.status_code == 200
    return review.json()


def test_approved_canary_routes_live_implicit_policy_traffic() -> None:
    with TestClient(app) as client:
        policy = _activate_canary(client)
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Use live rollout"}],
            },
        )
        metrics = client.get(
            f"/v1/policy-workflows/{policy['id']}/rollout-metrics",
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200
    receipt = response.json()["inference_receipt"]
    assert receipt["selected_model"] == "mock/quality"
    assert receipt["policy_version_id"] == policy["id"]
    assert receipt["rollout_group"] == "canary"
    assert metrics.status_code == 200
    assert metrics.json()["canary"]["requests"] == 1
    assert metrics.json()["canary"]["errors"] == 0


def test_explicit_client_policy_bypasses_canary() -> None:
    with TestClient(app) as client:
        _activate_canary(client)
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Keep my policy"}],
                "inferenceiq": {"policy": "cost"},
            },
        )

    receipt = response.json()["inference_receipt"]
    assert receipt["selected_model"] == "mock/fast"
    assert receipt["policy_version_id"] is None
    assert receipt["rollout_group"] is None


def test_canary_automatically_rolls_back_after_guardrail_violation() -> None:
    with TestClient(app) as client:
        policy = _activate_canary(
            client,
            guardrail_min_requests=1,
            max_error_rate=0,
        )
        app.state.providers["mock"] = FailingMockProvider()
        failed = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Trigger rollback"}],
            },
        )
        state = client.get(
            f"/v1/policy-workflows/{policy['id']}", headers=ADMIN_HEADERS
        )
        metrics = client.get(
            f"/v1/policy-workflows/{policy['id']}/rollout-metrics",
            headers=ADMIN_HEADERS,
        )

    assert failed.status_code == 503
    assert state.json()["status"] == "rolled_back"
    assert "error rate" in state.json()["rollback_reason"]
    assert metrics.json()["canary"]["errors"] == 1
