import asyncio
import json

from app.inference.models import InferenceReceipt
from app.observability import LangSmithObserver, build_trace_data
from app.schemas.chat import ChatCompletionResponse, Usage


def response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="completion-id",
        created=1,
        model="mock/fast",
        choices=[],
        usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        provider="mock",
        inference_receipt=InferenceReceipt(
            selected_model="mock/fast",
            selected_provider="mock",
            policy="cost",
            reason="test",
            candidates=[],
            actual_input_tokens=5,
            actual_output_tokens=3,
            actual_cost_usd=0.001,
            provider_latency_ms=12.5,
        ),
    )


def test_trace_payload_contains_metrics_but_no_content() -> None:
    trace_data = build_trace_data("safe-fingerprint", response())
    serialized = json.dumps(trace_data)

    assert "safe-fingerprint" in serialized
    assert "mock/fast" in serialized
    assert '"content_recorded": false' in serialized
    assert "secret prompt text" not in serialized


def test_disabled_observer_is_a_noop() -> None:
    observer = LangSmithObserver(enabled=False, api_key=None, project="test")

    trace_id = asyncio.run(
        observer.record_inference(
            request_fingerprint="fingerprint",
            response=response(),
        )
    )
    feedback_sent = asyncio.run(
        observer.record_feedback(trace_id=None, score=1, outcome="accepted")
    )

    assert trace_id is None
    assert feedback_sent is False
