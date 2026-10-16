from fastapi.testclient import TestClient

from app.main import app

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


def test_simulator_replays_history_with_a_new_policy() -> None:
    with TestClient(app) as client:
        for index in range(2):
            completion = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "user", "content": f"Simulation request {index}"}
                    ],
                    "inferenceiq": {"policy": "cost"},
                },
            )
            receipt_id = completion.json()["inference_receipt"]["receipt_id"]
            client.post(
                "/v1/feedback",
                json={
                    "receipt_id": receipt_id,
                    "score": 0.55,
                    "outcome": "accepted",
                },
            )

        simulation = client.post(
            "/v1/simulations",
            headers=ADMIN_HEADERS,
            json={"policy": "quality", "limit": 100},
        )

    assert simulation.status_code == 200
    body = simulation.json()
    assert body["sample_size"] == 2
    assert body["changed_requests"] == 2
    assert body["changed_percent"] == 100
    assert body["current"]["model_distribution"] == {"mock/fast": 2}
    assert body["simulated"]["model_distribution"] == {"mock/quality": 2}
    assert body["delta"]["cost_percent"] > 0
    assert body["delta"]["quality_points"] > 0


def test_simulator_handles_empty_history() -> None:
    with TestClient(app) as client:
        simulation = client.post(
            "/v1/simulations",
            headers=ADMIN_HEADERS,
            json={"policy": "balanced"},
        )

    assert simulation.status_code == 200
    assert simulation.json()["sample_size"] == 0
