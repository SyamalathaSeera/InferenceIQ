from fastapi.testclient import TestClient

from app.main import app

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


def _create_project_and_key(
    client: TestClient,
    *,
    name: str,
    rate_limit_rpm: int = 60,
    monthly_budget_usd: float | None = None,
) -> tuple[dict, dict]:
    project = client.post(
        "/v1/projects",
        headers=ADMIN_HEADERS,
        json={
            "name": name,
            "rate_limit_rpm": rate_limit_rpm,
            "monthly_budget_usd": monthly_budget_usd,
        },
    )
    assert project.status_code == 201
    key = client.post(
        f"/v1/projects/{project.json()['id']}/api-keys",
        headers=ADMIN_HEADERS,
        json={"name": "test-key"},
    )
    assert key.status_code == 201
    return project.json(), key.json()


def _completion(client: TestClient, secret: str | None = None):
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "mock/fast",
            "messages": [{"role": "user", "content": "Authenticated request"}],
        },
    )


def test_project_control_plane_requires_admin_key() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/projects",
            json={"name": "unauthorized-project"},
        )

    assert response.status_code == 401


def test_api_key_authenticates_and_attributes_usage() -> None:
    with TestClient(app) as client:
        project, key = _create_project_and_key(client, name="authenticated-project")
        app.state.settings.inferenceiq_auth_required = True
        try:
            missing = _completion(client)
            completed = _completion(client, key["secret"])
        finally:
            app.state.settings.inferenceiq_auth_required = False
        usage = client.get(
            f"/v1/projects/{project['id']}/usage",
            headers=ADMIN_HEADERS,
        )

    assert key["secret"].startswith("ii_live_")
    assert key["key_prefix"] == key["secret"][:16]
    assert missing.status_code == 401
    assert completed.status_code == 200
    assert usage.status_code == 200
    assert usage.json()["admitted_requests"] == 1
    assert usage.json()["completed_requests"] == 1
    assert usage.json()["input_tokens"] > 0


def test_rate_limit_rejects_requests_after_project_limit() -> None:
    with TestClient(app) as client:
        _, key = _create_project_and_key(
            client,
            name="rate-limited-project",
            rate_limit_rpm=1,
        )
        first = _completion(client, key["secret"])
        second = _completion(client, key["secret"])

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


def test_zero_budget_rejects_before_provider_call() -> None:
    with TestClient(app) as client:
        _, key = _create_project_and_key(
            client,
            name="zero-budget-project",
            monthly_budget_usd=0,
        )
        response = _completion(client, key["secret"])

    assert response.status_code == 402
    assert response.json()["detail"] == "Project monthly budget exhausted"


def test_revoked_api_key_cannot_be_used() -> None:
    with TestClient(app) as client:
        project, key = _create_project_and_key(client, name="revoked-key-project")
        revoked = client.post(
            f"/v1/projects/{project['id']}/api-keys/{key['id']}/revoke",
            headers=ADMIN_HEADERS,
        )
        response = _completion(client, key["secret"])

    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert response.status_code == 401


def test_feedback_cannot_cross_project_boundary() -> None:
    with TestClient(app) as client:
        _, first_key = _create_project_and_key(client, name="feedback-owner")
        _, second_key = _create_project_and_key(client, name="feedback-outsider")
        completion = _completion(client, first_key["secret"])
        receipt_id = completion.json()["inference_receipt"]["receipt_id"]
        rejected = client.post(
            "/v1/feedback",
            headers={"Authorization": f"Bearer {second_key['secret']}"},
            json={"receipt_id": receipt_id, "score": 0.5, "outcome": "accepted"},
        )
        accepted = client.post(
            "/v1/feedback",
            headers={"Authorization": f"Bearer {first_key['secret']}"},
            json={"receipt_id": receipt_id, "score": 0.9, "outcome": "accepted"},
        )

    assert rejected.status_code == 404
    assert accepted.status_code == 201
