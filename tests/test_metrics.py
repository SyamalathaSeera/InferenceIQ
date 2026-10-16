from app.inference.metrics import add_actuals, add_prediction, calculate_cost
from app.inference.models import InferenceReceipt, ModelProfile
from app.schemas.chat import ChatCompletionRequest, Usage


def profile(*, pricing_known: bool = True) -> ModelProfile:
    return ModelProfile(
        model_id="test/model",
        provider="test",
        quality_score=0.8,
        estimated_latency_ms=500,
        input_cost_per_million=2 if pricing_known else None,
        output_cost_per_million=10 if pricing_known else None,
    )


def receipt() -> InferenceReceipt:
    return InferenceReceipt(
        selected_model="test/model",
        selected_provider="test",
        policy="balanced",
        reason="test",
        candidates=[],
    )


def test_calculate_cost_uses_separate_input_and_output_prices() -> None:
    cost = calculate_cost(profile(), input_tokens=1_000, output_tokens=500)

    assert cost == 0.007


def test_unknown_pricing_returns_none_instead_of_zero() -> None:
    assert (
        calculate_cost(profile(pricing_known=False), input_tokens=100, output_tokens=50)
        is None
    )


def test_receipt_contains_prediction_and_actual_measurements() -> None:
    request = ChatCompletionRequest(
        model="test/model",
        messages=[{"role": "user", "content": "A short request"}],
        max_tokens=100,
    )
    predicted = add_prediction(receipt(), profile(), request)
    completed = add_actuals(
        predicted,
        profile(),
        Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        provider_latency_ms=123.456,
    )

    assert completed.assumed_output_tokens == 100
    assert completed.predicted_cost_usd is not None
    assert completed.actual_cost_usd == 0.00022
    assert completed.provider_latency_ms == 123.46
