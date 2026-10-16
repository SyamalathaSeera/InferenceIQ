from fastapi.testclient import TestClient

from app.main import app


def test_feedback_is_linked_to_a_saved_inference() -> None:
    with TestClient(app) as client:
        completion = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Explain caching query"}],
            },
        )
        receipt_id = completion.json()["inference_receipt"]["receipt_id"]

        feedback = client.post(
            "/v1/feedback",
            json={
                "receipt_id": receipt_id,
                "score": 0.9,
                "outcome": "accepted",
            },
        )

    assert feedback.status_code == 201
    assert feedback.json()["receipt_id"] == receipt_id
    assert feedback.json()["score"] == 0.9


def test_feedback_rejects_an_unknown_receipt() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/feedback",
            json={
                "receipt_id": "ir_missing",
                "score": 0.5,
                "outcome": "accepted",
            },
        )

    assert response.status_code == 404


def test_feedback_score_must_be_between_zero_and_one() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/feedback",
            json={
                "receipt_id": "ir_missing",
                "score": 1.5,
                "outcome": "accepted",
            },
        )

    assert response.status_code == 422
