from collections import Counter
from dataclasses import dataclass

from app.inference.engine import InferenceDecisionEngine, NoEligibleModelError
from app.inference.metrics import calculate_cost
from app.inference.models import InferenceOptions, ModelProfile
from app.schemas.simulation import (
    SimulationDelta,
    SimulationMetrics,
    SimulationRequest,
    SimulationResponse,
)


@dataclass
class HistoricalSample:
    selected_model: str
    candidates: list[dict]
    actual_input_tokens: int | None
    actual_output_tokens: int | None
    actual_cost_usd: float | None
    provider_latency_ms: float | None
    feedback_score: float | None


def simulate(
    samples: list[HistoricalSample],
    request: SimulationRequest,
) -> SimulationResponse:
    current_costs: list[float] = []
    simulated_costs: list[float] = []
    current_latencies: list[float] = []
    simulated_latencies: list[float] = []
    current_qualities: list[float] = []
    simulated_qualities: list[float] = []
    current_models: Counter[str] = Counter()
    simulated_models: Counter[str] = Counter()
    changed = 0
    processed = 0

    for sample in samples:
        profiles = _profiles_from_sample(sample)
        if not profiles:
            continue

        try:
            decision = InferenceDecisionEngine(profiles).decide(
                requested_model="auto",
                options=InferenceOptions(
                    policy=request.policy,
                    require_zero_data_retention=request.require_zero_data_retention,
                ),
            )
        except NoEligibleModelError:
            continue

        current_profile = next(
            (
                profile
                for profile in profiles
                if profile.model_id == sample.selected_model
            ),
            None,
        )
        if current_profile is None:
            continue

        simulated_profile = decision.model
        processed += 1
        current_models[current_profile.model_id] += 1
        simulated_models[simulated_profile.model_id] += 1
        if current_profile.model_id != simulated_profile.model_id:
            changed += 1

        _append_if_known(current_costs, sample.actual_cost_usd)
        _append_if_known(current_latencies, sample.provider_latency_ms)
        _append_if_known(
            current_qualities,
            sample.feedback_score
            if sample.feedback_score is not None
            else current_profile.quality_score,
        )

        simulated_cost = calculate_cost(
            simulated_profile,
            input_tokens=sample.actual_input_tokens or 0,
            output_tokens=sample.actual_output_tokens or 0,
        )
        _append_if_known(simulated_costs, simulated_cost)
        simulated_latencies.append(float(simulated_profile.estimated_latency_ms))
        simulated_qualities.append(simulated_profile.quality_score)

    current_metrics = _metrics(
        current_costs, current_latencies, current_qualities, current_models
    )
    simulated_metrics = _metrics(
        simulated_costs, simulated_latencies, simulated_qualities, simulated_models
    )
    return SimulationResponse(
        policy=request.policy,
        sample_size=processed,
        changed_requests=changed,
        changed_percent=round((changed / processed * 100) if processed else 0, 2),
        current=current_metrics,
        simulated=simulated_metrics,
        delta=SimulationDelta(
            cost_percent=_percent_change(
                current_metrics.total_cost_usd,
                simulated_metrics.total_cost_usd,
            ),
            latency_percent=_percent_change(
                current_metrics.average_latency_ms,
                simulated_metrics.average_latency_ms,
            ),
            quality_points=_difference(
                current_metrics.average_quality,
                simulated_metrics.average_quality,
            ),
        ),
        methodology=[
            "Current cost and latency use recorded request measurements.",
            "Simulated cost and latency use stored candidate profile estimates.",
            (
                "Current quality uses feedback when available, otherwise the stored "
                "estimate."
            ),
            "Simulation does not call any model or guarantee future performance.",
        ],
    )


def _profiles_from_sample(sample: HistoricalSample) -> list[ModelProfile]:
    profiles: list[ModelProfile] = []
    for candidate in sample.candidates:
        quality = candidate.get("quality_score")
        latency = candidate.get("estimated_latency_ms")
        if quality is None or latency is None:
            # Older records without candidate evidence cannot be replayed.
            continue
        profiles.append(
            ModelProfile(
                model_id=candidate["model"],
                provider=candidate["provider"],
                quality_score=quality,
                estimated_latency_ms=latency,
                input_cost_per_million=candidate.get("input_cost_per_million"),
                output_cost_per_million=candidate.get("output_cost_per_million"),
                zero_data_retention=candidate.get("zero_data_retention", False),
            )
        )
    return profiles


def _append_if_known(values: list[float], value: float | None) -> None:
    if value is not None:
        values.append(float(value))


def _metrics(
    costs: list[float],
    latencies: list[float],
    qualities: list[float],
    distribution: Counter[str],
) -> SimulationMetrics:
    return SimulationMetrics(
        total_cost_usd=round(sum(costs), 8) if costs else None,
        average_latency_ms=round(sum(latencies) / len(latencies), 2)
        if latencies
        else None,
        average_quality=round(sum(qualities) / len(qualities), 4)
        if qualities
        else None,
        model_distribution=dict(distribution),
    )


def _percent_change(current: float | None, simulated: float | None) -> float | None:
    if current is None or simulated is None or current == 0:
        return None
    return round((simulated - current) / current * 100, 2)


def _difference(current: float | None, simulated: float | None) -> float | None:
    if current is None or simulated is None:
        return None
    return round(simulated - current, 4)
