from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.chat import ChatMessage


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    expected_exact: str | None = None
    required_substrings: list[str] = Field(default_factory=list)
    forbidden_substrings: list[str] = Field(default_factory=list)
    category: str = "general"
    dataset_version: str = "legacy-v1"
    expected_number: float | None = Field(default=None, allow_inf_nan=False)
    number_tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    expected_json: dict[str, Any] | list[Any] | None = None
    reference_answer: str | None = None
    semantic_rubric: str | None = None

    @model_validator(mode="after")
    def requires_an_assertion(self) -> "EvaluationCase":
        if not (
            self.expected_exact is not None
            or self.required_substrings
            or self.forbidden_substrings
            or self.expected_number is not None
            or self.expected_json is not None
            or (self.reference_answer and self.semantic_rubric)
        ):
            raise ValueError("An evaluation case requires at least one assertion")
        return self


class VersionedDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1)
    description: str
    cases: list[EvaluationCase] = Field(min_length=1)


class CatalogPrice(BaseModel):
    input_cost_per_million: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    output_cost_per_million: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )


class PriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1)
    source: str
    simulated: bool = False
    models: dict[str, CatalogPrice]


class QualityComparison(BaseModel):
    baseline_arm: str
    comparison_arm: str
    quality_pass_rate_difference_pp: float | None
    cost_per_request_difference_usd: float | None
    cost_per_quality_passing_response_difference_usd: float | None
    p50_latency_difference_ms: float | None
    p95_latency_difference_ms: float | None


class EvaluationCaseResult(BaseModel):
    case_id: str
    passed: bool
    score: float | None
    evaluator_scores: dict[str, float]
    output: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None


class EvaluationReport(BaseModel):
    model: str
    total_cases: int
    passed_cases: int
    pass_rate: float | None
    average_score: float | None
    average_latency_ms: float
    total_input_tokens: int | None
    total_output_tokens: int | None
    results: list[EvaluationCaseResult]


class RequestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Window end must be >= start")
        return self


class FaultWindow(RequestWindow):
    models: list[Literal["mock/fast", "mock/balanced", "mock/quality"]] = Field(
        min_length=1
    )
    error: Literal["rate_limit", "timeout", "unavailable", "authentication"]
    delay_ms: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)


class FaultScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    request_interval_seconds: float = Field(
        default=1, gt=0, le=86400, allow_inf_nan=False
    )
    health_ewma_alpha: float = Field(default=0.2, gt=0, le=1, allow_inf_nan=False)
    circuit_failure_threshold: int = Field(default=3, ge=1)
    circuit_cooldown_seconds: int = Field(default=5, ge=1)
    faults: list[FaultWindow] = Field(min_length=1)
    recovery_windows: list[RequestWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def unambiguous(self):
        for index, window in enumerate(self.faults):
            for other in self.faults[index + 1 :]:
                if max(window.start, other.start) <= min(window.end, other.end) and set(
                    window.models
                ) & set(other.models):
                    raise ValueError("Fault windows cannot overlap on the same model")
        for index, window in enumerate(self.recovery_windows):
            others = self.faults + self.recovery_windows[index + 1 :]
            if any(
                max(window.start, other.start) <= min(window.end, other.end)
                for other in others
            ):
                raise ValueError("Recovery windows must be disjoint and fault-free")
        return self


class FaultAttemptResult(BaseModel):
    model: str
    success: bool
    status_code: int
    error_category: str | None = None
    retryable: bool | None = None
    circuit_state: str


class ReliabilityArmSummary(BaseModel):
    fault_window_requests: int
    fault_window_success_rate: float | None
    recovery_window_requests: int
    recovery_success_rate: float | None
    primary_failure_requests: int
    primary_failure_recovery_rate: float | None
    fallback_requests: int
    fallback_success_rate: float | None
    total_gateway_attempts: int
    mean_gateway_attempts: float
    circuit_blocked_requests: int
    circuit_avoidance_successes: int
    half_open_probes: int
    circuits_opened: int


class ReliabilityComparison(BaseModel):
    baseline_arm: str
    automatic_arm: str
    success_improvement_pp: float
    fault_window_success_improvement_pp: float | None
    recovery_success_improvement_pp: float | None


class BenchmarkCaseResult(BaseModel):
    arm: str
    case_id: str
    repetition: int
    requested_model: str
    selected_model: str | None = None
    response_model: str | None = None
    http_status: int | None = None
    success: bool = False
    error: str | None = None
    latency_ms: float | None
    submitted: bool = True
    gateway_attempts: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    completed_call_cost_usd: float | None = None
    request_cost_usd: float | None = None
    simulated: bool | None = None
    quality_score: float | None = None
    quality_passed: bool | None = None
    quality_missing_reason: str | None = None
    pricing_version: str | None = None
    routing_input_cost_per_million: float | None = None
    routing_output_cost_per_million: float | None = None
    pricing_matches_routing: bool | None = None
    judge_cost_usd: float | None = None
    judge_requested: bool = False
    evaluator_scores: dict[str, float] = Field(default_factory=dict)
    fault_request_index: int | None = None
    virtual_time_seconds: float | None = None
    fault_window: bool = False
    recovery_window: bool = False
    attempt_details: list[FaultAttemptResult] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)
    circuit_before: dict[str, str] = Field(default_factory=dict)
    circuit_after: dict[str, str] = Field(default_factory=dict)
    avoided_open_models: list[str] = Field(default_factory=list)


class BenchmarkArmSummary(BaseModel):
    arm: str
    requests: int
    successes: int
    success_rate: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    successful_p50_latency_ms: float | None
    successful_p95_latency_ms: float | None
    average_quality_score: float | None
    quality_scored_requests: int
    quality_pass_rate: float | None
    quality_unknown_requests: int = 0
    quality_passed_requests: int = 0
    submitted_requests: int = 0
    cost_per_request_usd: float | None = None
    cost_per_quality_passing_response_usd: float | None = None
    total_input_tokens: int | None
    total_output_tokens: int | None
    usage_known_requests: int
    total_cost_usd: float | None
    known_completed_call_cost_usd: float | None
    cost_known_requests: int
    simulated_requests: int
    model_distribution: dict[str, int]
    reliability: ReliabilityArmSummary | None = None


class BenchmarkReport(BaseModel):
    schema_version: int = 1
    dataset_sha256: str
    repetitions: int
    concurrency: int
    fixed_models: list[str]
    generation_parameters: dict[str, float | int]
    max_attempts: int
    client_timeout_seconds: dict[str, float | None]
    methodology: list[str]
    arms: list[BenchmarkArmSummary]
    results: list[BenchmarkCaseResult]
    fault_scenario: FaultScenario | None = None
    reliability_comparisons: list[ReliabilityComparison] = Field(default_factory=list)
    dataset_version: str | None = None
    evaluation_version: str | None = None
    quality_threshold: float = 1
    price_catalog: PriceCatalog | None = None
    price_catalog_sha256: str | None = None
    quality_comparisons: list[QualityComparison] = Field(default_factory=list)
    semantic_judge_model: str | None = None
    budget_usd: float | None = None
    reserved_budget_usd: float | None = None
    budget_halted: bool = False
    judge_requests: int = 0
    total_judge_cost_usd: float | None = None
    langsmith_experiments: list[dict[str, str | None]] = Field(default_factory=list)
