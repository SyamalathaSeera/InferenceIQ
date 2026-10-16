import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.persistence.models import ApiKeyRecord, AuditEvent
from app.privacy import protect_request
from app.schemas.chat import ChatCompletionRequest

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


def _project_and_key(
    client: TestClient, *, name: str, pii_policy: str
) -> tuple[dict, dict]:
    project = client.post(
        "/v1/projects",
        headers=ADMIN_HEADERS,
        json={"name": name, "pii_policy": pii_policy},
    ).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        headers=ADMIN_HEADERS,
        json={"name": "privacy-test"},
    ).json()
    return project, key


def test_privacy_detector_redacts_supported_types_and_validates_cards() -> None:
    request = ChatCompletionRequest(
        model="mock/fast",
        messages=[
            {
                "role": "user",
                "content": (
                    "Email sam@example.com, call 415-555-2671, SSN 123-45-6789, "
                    "card 4242 4242 4242 4242, invalid 4242 4242 4242 4241."
                ),
            }
        ],
    )

    result = protect_request(request, "redact")
    content = result.request.messages[0].content or ""

    assert result.detected_types == ["email", "payment_card", "phone", "ssn"]
    assert result.match_count == 4
    assert "sam@example.com" not in content
    assert "123-45-6789" not in content
    assert "4242 4242 4242 4242" not in content
    assert "4242 4242 4242 4241" in content


def test_redaction_protects_provider_and_creates_valid_audit_chain() -> None:
    with TestClient(app) as client:
        project, key = _project_and_key(
            client,
            name="redaction-project",
            pii_policy="redact",
        )
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key['secret']}"},
            json={
                "model": "mock/fast",
                "messages": [{"role": "user", "content": "Contact sam@example.com"}],
            },
        )
        events = client.get(
            f"/v1/projects/{project['id']}/audit-events",
            headers=ADMIN_HEADERS,
        )
        integrity = client.get(
            f"/v1/projects/{project['id']}/audit-integrity",
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert "sam@example.com" not in body["choices"][0]["message"]["content"]
    assert "[EMAIL_REDACTED]" in body["choices"][0]["message"]["content"]
    assert body["inference_receipt"]["pii_action"] == "redact"
    assert body["inference_receipt"]["pii_detected_types"] == ["email"]
    assert body["inference_receipt"]["pii_redaction_count"] == 1
    event_body = events.json()
    assert [event["sequence"] for event in event_body] == [1, 2, 3]
    assert event_body[-1]["event_type"] == "privacy.pii_redact"
    assert "sam@example.com" not in json.dumps(event_body)
    assert key["secret"] not in json.dumps(event_body)
    assert integrity.json()["valid"] is True


def test_block_policy_stops_pii_before_provider_call() -> None:
    with TestClient(app) as client:
        project, key = _project_and_key(
            client,
            name="blocking-project",
            pii_policy="block",
        )
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key['secret']}"},
            json={
                "model": "mock/fast",
                "messages": [{"role": "user", "content": "SSN 123-45-6789"}],
            },
        )
        events = client.get(
            f"/v1/projects/{project['id']}/audit-events",
            headers=ADMIN_HEADERS,
        ).json()

    assert response.status_code == 422
    assert response.json()["detail"]["detected_types"] == ["ssn"]
    assert events[-1]["event_type"] == "privacy.pii_block"


def test_audit_integrity_detects_database_tampering() -> None:
    async def tamper() -> None:
        async with app.state.database.session_factory() as session:
            event = await session.scalar(
                select(AuditEvent).order_by(AuditEvent.sequence).limit(1)
            )
            assert event is not None
            event.details = {"tampered": True}
            await session.commit()

    with TestClient(app) as client:
        project, _ = _project_and_key(
            client,
            name="tamper-test-project",
            pii_policy="detect",
        )
        asyncio.run(tamper())
        integrity = client.get(
            f"/v1/projects/{project['id']}/audit-integrity",
            headers=ADMIN_HEADERS,
        )

    assert integrity.status_code == 200
    assert integrity.json()["valid"] is False
    assert integrity.json()["first_invalid_sequence"] == 1


def test_database_stores_api_key_hash_not_raw_secret() -> None:
    async def load_key(key_id: str) -> ApiKeyRecord:
        async with app.state.database.session_factory() as session:
            record = await session.get(ApiKeyRecord, key_id)
            assert record is not None
            return record

    with TestClient(app) as client:
        _, key = _project_and_key(
            client,
            name="hashed-key-project",
            pii_policy="off",
        )
        stored = asyncio.run(load_key(key["id"]))

    assert stored.key_hash != key["secret"]
    assert len(stored.key_hash) == 64
