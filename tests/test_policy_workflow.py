import asyncio
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from langgraph.types import Command

from app.main import app
from app.policy.checkpoints import open_checkpointer
from app.policy.workflow import create_policy_graph

ADMIN_HEADERS = {"X-InferenceIQ-Admin-Key": "test-admin-key"}


def _create_history(client: TestClient) -> None:
    client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "Explain cache invalidation"}],
            "inferenceiq": {"policy": "cost"},
        },
    )


def test_policy_workflow_pauses_for_review_then_promotes_to_canary() -> None:
    with TestClient(app) as client:
        _create_history(client)
        proposal = client.post(
            "/v1/policy-workflows",
            headers=ADMIN_HEADERS,
            json={"name": "quality-candidate", "policy": "quality"},
        )
        body = proposal.json()

        assert proposal.status_code == 201
        assert body["version"] == 1
        assert body["status"] == "pending_approval"
        assert body["review_required"] is True
        assert body["simulation"]["sample_size"] == 1

        review = client.post(
            f"/v1/policy-workflows/{body['id']}/review",
            headers=ADMIN_HEADERS,
            json={"approved": True, "reviewed_by": "portfolio-owner"},
        )

        assert review.status_code == 200
        assert review.json()["status"] == "canary"
        assert review.json()["reviewed_by"] == "portfolio-owner"


def test_policy_workflow_can_be_rejected() -> None:
    with TestClient(app) as client:
        _create_history(client)
        proposal = client.post(
            "/v1/policy-workflows",
            headers=ADMIN_HEADERS,
            json={"name": "latency-candidate", "policy": "latency"},
        ).json()
        review = client.post(
            f"/v1/policy-workflows/{proposal['id']}/review",
            headers=ADMIN_HEADERS,
            json={"approved": False, "reviewed_by": "reviewer"},
        )

    assert review.status_code == 200
    assert review.json()["status"] == "rejected"


def test_policy_workflow_rejects_empty_simulation_history() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/policy-workflows",
            headers=ADMIN_HEADERS,
            json={"name": "empty-history", "policy": "balanced"},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "invalid"
    assert response.json()["review_required"] is False
    assert response.json()["validation_errors"]


def test_paused_workflow_survives_checkpoint_reopen() -> None:
    async def exercise() -> None:
        sample = {
            "selected_model": "mock/fast",
            "candidates": [
                {
                    "model": "mock/fast",
                    "provider": "mock",
                    "quality_score": 0.6,
                    "estimated_latency_ms": 50,
                    "input_cost_per_million": 0.1,
                    "output_cost_per_million": 0.2,
                    "zero_data_retention": True,
                }
            ],
            "actual_input_tokens": 10,
            "actual_output_tokens": 10,
            "actual_cost_usd": 0.000003,
            "provider_latency_ms": 45,
            "feedback_score": 0.8,
        }
        config = {"configurable": {"thread_id": "durable-policy-test"}}

        with TemporaryDirectory() as directory:
            checkpoint_path = f"{directory}/checkpoints.sqlite"
            async with open_checkpointer(checkpoint_path) as checkpointer:
                graph = create_policy_graph(checkpointer)
                paused = await graph.ainvoke(
                    {
                        "policy_id": "pol_durable",
                        "name": "durable",
                        "version": 1,
                        "policy": "quality",
                        "require_zero_data_retention": False,
                        "samples": [sample],
                    },
                    config,
                )
                assert paused["status"] == "pending_approval"

            async with open_checkpointer(checkpoint_path) as checkpointer:
                graph = create_policy_graph(checkpointer)
                resumed = await graph.ainvoke(Command(resume=True), config)
                assert resumed["status"] == "canary"

    asyncio.run(exercise())
