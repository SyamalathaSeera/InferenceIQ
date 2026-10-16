import json

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_completion_is_openai_compatible() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "demo-model",
                "messages": [{"role": "user", "content": "Hello gateway"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "demo-model"
    assert body["provider"] == "mock"
    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": "Mock response: Hello gateway",
    }
    assert body["usage"]["total_tokens"] > 0
    assert body["inference_receipt"]["policy"] == "explicit"


def test_auto_model_returns_an_inference_receipt() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Explain caching"}],
                "inferenceiq": {"policy": "cost"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "mock/fast"
    assert body["inference_receipt"]["selected_model"] == "mock/fast"
    assert body["inference_receipt"]["policy"] == "cost"
    assert len(body["inference_receipt"]["candidates"]) == 3
    assert body["inference_receipt"]["predicted_cost_usd"] is not None
    assert body["inference_receipt"]["actual_cost_usd"] is not None
    assert body["inference_receipt"]["provider_latency_ms"] >= 0


def test_unconfigured_provider_returns_clear_error() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "anthropic/test-model",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


def test_request_requires_at_least_one_message() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": []},
        )

    assert response.status_code == 422


def test_chat_completion_streams_openai_style_events() -> None:
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "demo-model",
                "messages": [{"role": "user", "content": "Hello streaming"}],
                "stream": True,
            },
        ) as response:
            lines = [line for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-inferenceiq-model"] == "demo-model"
    assert response.headers["x-inferenceiq-receipt-id"].startswith("ir_")
    assert lines[-1] == "data: [DONE]"
    assert any('"object":"chat.completion.chunk"' in line for line in lines)
    events = [
        json.loads(line.removeprefix("data: "))
        for line in lines
        if line != "data: [DONE]"
    ]
    content = "".join(
        event["choices"][0]["delta"].get("content", "") for event in events
    )
    assert content == "Mock response: Hello streaming"
