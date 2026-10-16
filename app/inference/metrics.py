from app.inference.models import InferenceReceipt, ModelProfile
from app.schemas.chat import ChatCompletionRequest, Usage

DEFAULT_ASSUMED_OUTPUT_TOKENS = 256


def estimate_input_tokens(request: ChatCompletionRequest) -> int:
    """Return a transparent approximation until provider tokenizers are added."""

    characters = sum(len(message.content or "") for message in request.messages)
    message_overhead = len(request.messages) * 4
    return max(1, round(characters / 4) + message_overhead)


def add_prediction(
    receipt: InferenceReceipt,
    profile: ModelProfile,
    request: ChatCompletionRequest,
) -> InferenceReceipt:
    predicted_input_tokens = estimate_input_tokens(request)
    assumed_output_tokens = request.max_tokens or DEFAULT_ASSUMED_OUTPUT_TOKENS
    predicted_cost = calculate_cost(
        profile,
        input_tokens=predicted_input_tokens,
        output_tokens=assumed_output_tokens,
    )
    return receipt.model_copy(
        update={
            "predicted_input_tokens": predicted_input_tokens,
            "assumed_output_tokens": assumed_output_tokens,
            "predicted_cost_usd": predicted_cost,
        }
    )


def add_actuals(
    receipt: InferenceReceipt,
    profile: ModelProfile,
    usage: Usage,
    *,
    provider_latency_ms: float,
) -> InferenceReceipt:
    actual_cost = calculate_cost(
        profile,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
    )
    return receipt.model_copy(
        update={
            "actual_input_tokens": usage.prompt_tokens,
            "actual_output_tokens": usage.completion_tokens,
            "actual_cost_usd": actual_cost,
            "provider_latency_ms": round(provider_latency_ms, 2),
        }
    )


def calculate_cost(
    profile: ModelProfile,
    *,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    if (
        profile.input_cost_per_million is None
        or profile.output_cost_per_million is None
    ):
        return None

    cost = (
        input_tokens * profile.input_cost_per_million
        + output_tokens * profile.output_cost_per_million
    ) / 1_000_000
    return round(cost, 8)
