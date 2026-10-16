import asyncio
from typing import Any
from uuid import UUID

import langsmith as ls
from langsmith import Client

from app.schemas.chat import ChatCompletionResponse


class LangSmithObserver:
    """Best-effort, metadata-only LangSmith integration."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str | None,
        project: str,
    ) -> None:
        self.enabled = enabled and bool(api_key)
        self.project = project
        self._client = Client(api_key=api_key) if self.enabled else None

    async def record_inference(
        self,
        *,
        request_fingerprint: str,
        response: ChatCompletionResponse,
    ) -> str | None:
        if not self.enabled or self._client is None:
            return None
        try:
            return await asyncio.to_thread(
                self._record_inference_sync,
                request_fingerprint,
                response,
            )
        except Exception:
            # Observability must never take down inference traffic.
            return None

    async def record_feedback(
        self,
        *,
        trace_id: str | None,
        score: float,
        outcome: str,
    ) -> bool:
        if not self.enabled or self._client is None or trace_id is None:
            return False
        try:
            await asyncio.to_thread(
                self._client.create_feedback,
                key="user_feedback",
                score=score,
                trace_id=UUID(trace_id),
                comment=outcome,
            )
            return True
        except Exception:
            return False

    def _record_inference_sync(
        self,
        request_fingerprint: str,
        response: ChatCompletionResponse,
    ) -> str:
        inputs, outputs, metadata = build_trace_data(request_fingerprint, response)
        with ls.tracing_context(
            enabled=True,
            client=self._client,
            project_name=self.project,
        ):
            with ls.trace(
                "InferenceIQ inference",
                run_type="chain",
                inputs=inputs,
                metadata=metadata,
            ) as run:
                run.end(outputs=outputs)
                return str(run.id)


def build_trace_data(
    request_fingerprint: str,
    response: ChatCompletionResponse,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    receipt = response.inference_receipt
    if receipt is None:
        raise ValueError("A completed inference receipt is required")

    inputs = {
        "request_fingerprint": request_fingerprint,
        "policy": receipt.policy,
        "candidate_count": len(receipt.candidates),
    }
    outputs = {
        "receipt_id": receipt.receipt_id,
        "selected_model": receipt.selected_model,
        "selected_provider": receipt.selected_provider,
        "input_tokens": receipt.actual_input_tokens,
        "output_tokens": receipt.actual_output_tokens,
        "cost_usd": receipt.actual_cost_usd,
        "latency_ms": receipt.provider_latency_ms,
        "fallback_count": len(receipt.fallbacks),
        "policy_version_id": receipt.policy_version_id,
        "rollout_group": receipt.rollout_group,
    }
    metadata = {
        "inferenceiq_receipt_id": receipt.receipt_id,
        "model": receipt.selected_model,
        "provider": receipt.selected_provider,
        "policy": receipt.policy,
        "content_recorded": False,
        "policy_version_id": receipt.policy_version_id,
        "rollout_group": receipt.rollout_group,
    }
    return inputs, outputs, metadata
