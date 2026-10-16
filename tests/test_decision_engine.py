import pytest

from app.config import Settings
from app.inference.catalog import create_model_catalog
from app.inference.engine import InferenceDecisionEngine, NoEligibleModelError
from app.inference.models import InferenceOptions, ModelHealth, ModelProfile


@pytest.fixture
def engine() -> InferenceDecisionEngine:
    return InferenceDecisionEngine(create_model_catalog(Settings(provider="mock")))


def test_cost_policy_selects_cheapest_model(engine: InferenceDecisionEngine) -> None:
    decision = engine.decide(
        requested_model="auto",
        options=InferenceOptions(policy="cost"),
    )

    assert decision.model.model_id == "mock/fast"
    assert decision.receipt.policy == "cost"


def test_quality_policy_selects_highest_quality_model(
    engine: InferenceDecisionEngine,
) -> None:
    decision = engine.decide(
        requested_model="auto",
        options=InferenceOptions(policy="quality"),
    )

    assert decision.model.model_id == "mock/quality"


def test_privacy_constraint_rejects_non_retention_model(
    engine: InferenceDecisionEngine,
) -> None:
    decision = engine.decide(
        requested_model="auto",
        options=InferenceOptions(
            policy="quality",
            require_zero_data_retention=True,
        ),
    )

    assert decision.model.model_id != "mock/quality"
    rejected = next(
        candidate
        for candidate in decision.receipt.candidates
        if candidate.model == "mock/quality"
    )
    assert rejected.rejected_reason == "zero_data_retention_required"


def test_no_eligible_model_raises_clear_error() -> None:
    engine = InferenceDecisionEngine(
        [
            ModelProfile(
                model_id="retaining-model",
                provider="mock",
                quality_score=0.9,
                estimated_latency_ms=500,
                input_cost_per_million=1,
                output_cost_per_million=2,
                zero_data_retention=False,
            )
        ]
    )

    with pytest.raises(NoEligibleModelError):
        engine.decide(
            requested_model="auto",
            options=InferenceOptions(require_zero_data_retention=True),
        )


def test_explicit_model_bypasses_automatic_selection(
    engine: InferenceDecisionEngine,
) -> None:
    decision = engine.decide(
        requested_model="my-specific-model",
        options=InferenceOptions(policy="cost"),
    )

    assert decision.model.model_id == "my-specific-model"
    assert decision.receipt.policy == "explicit"


def test_explicit_prefixed_model_selects_its_provider(
    engine: InferenceDecisionEngine,
) -> None:
    decision = engine.decide(
        requested_model="anthropic/test-model",
        options=InferenceOptions(),
    )

    assert decision.model.provider == "anthropic"


def test_open_circuit_is_excluded_from_automatic_routing(
    engine: InferenceDecisionEngine,
) -> None:
    decision = engine.decide(
        requested_model="auto",
        options=InferenceOptions(policy="quality"),
        health={
            "mock/quality": ModelHealth(
                model_id="mock/quality",
                provider="mock",
                health_score=0.2,
                circuit_state="open",
                consecutive_failures=3,
            )
        },
    )

    assert decision.model.model_id != "mock/quality"
    rejected = next(
        candidate
        for candidate in decision.receipt.candidates
        if candidate.model == "mock/quality"
    )
    assert rejected.eligible is False
    assert rejected.rejected_reason == "circuit_open"
    assert rejected.health_score == 0.2
