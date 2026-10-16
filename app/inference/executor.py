from collections.abc import Callable
from time import perf_counter

from app.inference.metrics import add_actuals, add_prediction
from app.inference.models import FallbackAttempt, InferenceDecision
from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


async def execute_completion(
    request: ChatCompletionRequest,
    decision: InferenceDecision,
    providers: dict[str, ChatProvider],
    attempt_observer: Callable[[str, str, bool, float, str | None], None] | None = None,
) -> ChatCompletionResponse:
    """Execute ranked candidates with bounded, classified fallback."""

    attempts = decision.ranked_models[: request.inferenceiq.max_attempts]
    fallback_events: list[FallbackAttempt] = []
    last_error: ProviderError | None = None

    for index, profile in enumerate(attempts):
        provider = providers.get(profile.provider)
        if provider is None:
            error = ProviderError(
                f"Provider '{profile.provider}' is not configured.",
                status_code=400,
                category="configuration",
                retryable=False,
            )
            if attempt_observer is not None:
                attempt_observer(
                    profile.model_id,
                    profile.provider,
                    False,
                    0.0,
                    error.category,
                )
            if index == 0:
                raise error
            break

        provider_request = request.model_copy(update={"model": profile.model_id})
        started_at = perf_counter()
        try:
            response = await provider.complete(provider_request)
        except ProviderError as error:
            elapsed_ms = (perf_counter() - started_at) * 1000
            if attempt_observer is not None:
                attempt_observer(
                    profile.model_id,
                    profile.provider,
                    False,
                    elapsed_ms,
                    error.category,
                )
            last_error = error
            fallback_events.append(
                FallbackAttempt(
                    model=profile.model_id,
                    provider=profile.provider,
                    error_category=error.category,
                    status_code=error.status_code,
                    latency_ms=round(elapsed_ms, 2),
                )
            )
            if not error.retryable:
                raise
            continue

        elapsed_ms = (perf_counter() - started_at) * 1000
        if attempt_observer is not None:
            attempt_observer(
                profile.model_id,
                profile.provider,
                True,
                elapsed_ms,
                None,
            )
        receipt = decision.receipt.model_copy(
            update={
                "selected_model": profile.model_id,
                "selected_provider": profile.provider,
                "fallbacks": fallback_events,
                "reason": (
                    decision.receipt.reason
                    if not fallback_events
                    else (
                        f"The initial model failed; completed with "
                        f"fallback candidate {profile.model_id}."
                    )
                ),
            }
        )
        receipt = add_prediction(receipt, profile, provider_request)
        response.inference_receipt = add_actuals(
            receipt,
            profile,
            response.usage,
            provider_latency_ms=elapsed_ms,
        )
        return response

    if last_error is not None:
        raise last_error
    raise ProviderError("No configured fallback candidate is available.")
