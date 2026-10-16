from app.inference.models import (
    CandidateEvaluation,
    InferenceDecision,
    InferenceOptions,
    InferenceReceipt,
    ModelHealth,
    ModelProfile,
    PolicyName,
)

POLICY_WEIGHTS: dict[PolicyName, tuple[float, float, float]] = {
    # Each tuple is: quality, cost, latency.
    "balanced": (0.50, 0.25, 0.25),
    "cost": (0.05, 0.90, 0.05),
    "latency": (0.05, 0.05, 0.90),
    "quality": (0.90, 0.05, 0.05),
}


class NoEligibleModelError(Exception):
    """Raised when every model violates a required inference constraint."""


class CircuitOpenError(Exception):
    """Raised when an explicitly requested model has an open circuit."""


class InferenceDecisionEngine:
    def __init__(self, profiles: list[ModelProfile]) -> None:
        if not profiles:
            raise ValueError("At least one model profile is required")
        self._profiles = profiles

    def decide(
        self,
        *,
        requested_model: str | None,
        options: InferenceOptions,
        health: dict[str, ModelHealth] | None = None,
    ) -> InferenceDecision:
        health = health or {}
        if requested_model and requested_model != "auto":
            return self._explicit_decision(requested_model, health)

        candidates: list[CandidateEvaluation] = []
        eligible_profiles: list[ModelProfile] = []

        for profile in self._profiles:
            model_health = self._health_for(profile, health)
            if model_health.circuit_state == "open":
                candidates.append(
                    CandidateEvaluation(
                        model=profile.model_id,
                        provider=profile.provider,
                        eligible=False,
                        rejected_reason="circuit_open",
                        **self._profile_evidence(profile, model_health),
                    )
                )
            elif (
                options.require_zero_data_retention and not profile.zero_data_retention
            ):
                candidates.append(
                    CandidateEvaluation(
                        model=profile.model_id,
                        provider=profile.provider,
                        eligible=False,
                        rejected_reason="zero_data_retention_required",
                        **self._profile_evidence(profile, model_health),
                    )
                )
            else:
                eligible_profiles.append(profile)

        if not eligible_profiles:
            raise NoEligibleModelError(
                "No model satisfies the requested inference constraints."
            )

        costs = [
            cost
            for profile in eligible_profiles
            if (cost := self._blended_cost(profile)) is not None
        ]
        latencies = [
            self._effective_latency(profile, self._health_for(profile, health))
            for profile in eligible_profiles
        ]
        quality_weight, cost_weight, latency_weight = POLICY_WEIGHTS[options.policy]

        scored: list[tuple[ModelProfile, float]] = []
        for profile in eligible_profiles:
            model_health = self._health_for(profile, health)
            blended_cost = self._blended_cost(profile)
            cost_score = (
                self._inverse_normalize(blended_cost, costs)
                if blended_cost is not None and costs
                else 0.5
            )
            latency_score = self._inverse_normalize(
                self._effective_latency(profile, model_health), latencies
            )
            base_score = (
                quality_weight * profile.quality_score
                + cost_weight * cost_score
                + latency_weight * latency_score
            )
            score = base_score * (0.5 + 0.5 * model_health.health_score)
            scored.append((profile, score))
            candidates.append(
                CandidateEvaluation(
                    model=profile.model_id,
                    provider=profile.provider,
                    eligible=True,
                    score=round(score, 4),
                    **self._profile_evidence(profile, model_health),
                )
            )

        selected, selected_score = max(scored, key=lambda item: item[1])
        ranked_models = [
            profile
            for profile, _ in sorted(scored, key=lambda item: item[1], reverse=True)
        ]
        candidates.sort(
            key=lambda candidate: (
                candidate.score if candidate.score is not None else -1
            ),
            reverse=True,
        )

        return InferenceDecision(
            model=selected,
            ranked_models=ranked_models,
            receipt=InferenceReceipt(
                selected_model=selected.model_id,
                selected_provider=selected.provider,
                policy=options.policy,
                reason=(
                    f"Highest {options.policy} policy score "
                    f"({selected_score:.4f}) among eligible models."
                ),
                candidates=candidates,
            ),
        )

    def _explicit_decision(
        self,
        requested_model: str,
        health: dict[str, ModelHealth],
    ) -> InferenceDecision:
        profile = next(
            (
                profile
                for profile in self._profiles
                if profile.model_id == requested_model
            ),
            None,
        )
        if profile is None:
            # Explicit model names remain usable while the catalog is still being built.
            provider_prefix = requested_model.partition("/")[0]
            known_providers = {item.provider for item in self._profiles}
            known_providers.update({"openai", "anthropic", "ollama", "mock"})
            provider = (
                provider_prefix
                if "/" in requested_model and provider_prefix in known_providers
                else self._profiles[0].provider
            )
            profile = ModelProfile(
                model_id=requested_model,
                provider=provider,
                quality_score=0.5,
                estimated_latency_ms=1000,
            )
        model_health = self._health_for(profile, health)
        if model_health.circuit_state == "open":
            raise CircuitOpenError(
                f"Circuit is open for explicitly requested model '{requested_model}'."
            )

        return InferenceDecision(
            model=profile,
            ranked_models=[profile],
            receipt=InferenceReceipt(
                selected_model=profile.model_id,
                selected_provider=profile.provider,
                policy="explicit",
                reason="The client explicitly requested this model.",
                candidates=[
                    CandidateEvaluation(
                        model=profile.model_id,
                        provider=profile.provider,
                        eligible=True,
                        **self._profile_evidence(profile, model_health),
                    )
                ],
            ),
        )

    @staticmethod
    def _blended_cost(profile: ModelProfile) -> float | None:
        if (
            profile.input_cost_per_million is None
            or profile.output_cost_per_million is None
        ):
            return None
        return (profile.input_cost_per_million + profile.output_cost_per_million) / 2

    @staticmethod
    def _inverse_normalize(value: float, values: list[float]) -> float:
        minimum = min(values)
        maximum = max(values)
        if minimum == maximum:
            return 1.0
        return 1 - ((value - minimum) / (maximum - minimum))

    @staticmethod
    def _profile_evidence(profile: ModelProfile, health: ModelHealth) -> dict:
        return {
            "quality_score": profile.quality_score,
            "estimated_latency_ms": profile.estimated_latency_ms,
            "input_cost_per_million": profile.input_cost_per_million,
            "output_cost_per_million": profile.output_cost_per_million,
            "zero_data_retention": profile.zero_data_retention,
            "health_score": health.health_score,
            "circuit_state": health.circuit_state,
            "observed_latency_ms": health.observed_latency_ms,
            "consecutive_failures": health.consecutive_failures,
        }

    @staticmethod
    def _health_for(
        profile: ModelProfile,
        health: dict[str, ModelHealth],
    ) -> ModelHealth:
        return health.get(
            profile.model_id,
            ModelHealth(model_id=profile.model_id, provider=profile.provider),
        )

    @staticmethod
    def _effective_latency(profile: ModelProfile, health: ModelHealth) -> float:
        return health.observed_latency_ms or float(profile.estimated_latency_ms)
