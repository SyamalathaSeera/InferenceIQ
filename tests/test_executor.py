import asyncio

import pytest

from app.inference.engine import InferenceDecisionEngine
from app.inference.executor import execute_completion
from app.inference.models import InferenceOptions, ModelProfile
from app.providers.base import ProviderError
from app.providers.mock import MockProvider
from app.schemas.chat import ChatCompletionRequest


class FailingProvider(MockProvider):
    def __init__(self, *, retryable: bool = True) -> None:
        self.retryable = retryable

    async def complete(self, request):
        raise ProviderError(
            "simulated failure",
            status_code=503,
            category="unavailable",
            retryable=self.retryable,
        )


def profiles() -> list[ModelProfile]:
    return [
        ModelProfile(
            model_id="primary/model",
            provider="primary",
            quality_score=0.9,
            estimated_latency_ms=200,
            input_cost_per_million=1,
            output_cost_per_million=2,
        ),
        ModelProfile(
            model_id="fallback/model",
            provider="fallback",
            quality_score=0.7,
            estimated_latency_ms=400,
            input_cost_per_million=1,
            output_cost_per_million=2,
        ),
    ]


def request(*, max_attempts: int = 2) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="auto",
        messages=[{"role": "user", "content": "Hello"}],
        inferenceiq=InferenceOptions(policy="quality", max_attempts=max_attempts),
    )


def decision_for(body: ChatCompletionRequest):
    return InferenceDecisionEngine(profiles()).decide(
        requested_model=body.model,
        options=body.inferenceiq,
    )


def test_retryable_failure_uses_next_ranked_model() -> None:
    body = request()
    response = asyncio.run(
        execute_completion(
            body,
            decision_for(body),
            {
                "primary": FailingProvider(),
                "fallback": MockProvider(),
            },
        )
    )

    receipt = response.inference_receipt
    assert receipt is not None
    assert receipt.selected_model == "fallback/model"
    assert receipt.selected_provider == "fallback"
    assert len(receipt.fallbacks) == 1
    assert receipt.fallbacks[0].error_category == "unavailable"


def test_non_retryable_failure_stops_immediately() -> None:
    body = request()

    with pytest.raises(ProviderError):
        asyncio.run(
            execute_completion(
                body,
                decision_for(body),
                {
                    "primary": FailingProvider(retryable=False),
                    "fallback": MockProvider(),
                },
            )
        )


def test_max_attempts_prevents_unbounded_fallback() -> None:
    body = request(max_attempts=1)

    with pytest.raises(ProviderError):
        asyncio.run(
            execute_completion(
                body,
                decision_for(body),
                {
                    "primary": FailingProvider(),
                    "fallback": MockProvider(),
                },
            )
        )
