from fastapi.testclient import TestClient

from app.main import _is_unsafe_production_secret, app


def test_operational_endpoints_and_security_headers() -> None:
    with TestClient(app) as client:
        health = client.get("/health", headers={"X-Request-ID": "trace-test"})
        ready = client.get("/ready")
        metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.headers["x-request-id"] == "trace-test"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert ready.json() == {"status": "ready"}
    assert "inferenceiq_http_requests_total" in metrics.text


def test_sensitive_control_plane_endpoints_require_admin_key() -> None:
    with TestClient(app) as client:
        simulation = client.post(
            "/v1/simulations",
            json={"policy": "balanced"},
        )
        policies = client.get("/v1/policy-workflows")

    assert simulation.status_code == 401
    assert policies.status_code == 401


def test_production_secret_validation_rejects_defaults_and_placeholders() -> None:
    assert _is_unsafe_production_secret(None, development_value="dev-secret")
    assert _is_unsafe_production_secret("short", development_value="dev-secret")
    assert _is_unsafe_production_secret("dev-secret", development_value="dev-secret")
    assert _is_unsafe_production_secret(
        "replace-with-at-least-32-random-characters",
        development_value="dev-secret",
    )
    assert not _is_unsafe_production_secret(
        "a-strong-random-secret-with-32-characters",
        development_value="dev-secret",
    )
