from pydantic import BaseModel, Field, model_validator

from app.schemas.chat import ChatMessage


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    expected_exact: str | None = None
    required_substrings: list[str] = Field(default_factory=list)
    forbidden_substrings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def requires_an_assertion(self) -> "EvaluationCase":
        if not (
            self.expected_exact or self.required_substrings or self.forbidden_substrings
        ):
            raise ValueError("An evaluation case requires at least one assertion")
        return self


class EvaluationCaseResult(BaseModel):
    case_id: str
    passed: bool
    score: float
    evaluator_scores: dict[str, float]
    output: str
    latency_ms: float
    input_tokens: int
    output_tokens: int


class EvaluationReport(BaseModel):
    model: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    average_score: float
    average_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int
    results: list[EvaluationCaseResult]


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
    latency_ms: float
    gateway_attempts: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    completed_call_cost_usd: float | None = None
    request_cost_usd: float | None = None
    simulated: bool | None = None
    quality_score: float | None = None
    evaluator_scores: dict[str, float] = Field(default_factory=dict)


class BenchmarkArmSummary(BaseModel):
    arm: str
    requests: int
    successes: int
    success_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    successful_p50_latency_ms: float | None
    successful_p95_latency_ms: float | None
    average_quality_score: float | None
    quality_scored_requests: int
    quality_pass_rate: float
    total_input_tokens: int | None
    total_output_tokens: int | None
    usage_known_requests: int
    total_cost_usd: float | None
    known_completed_call_cost_usd: float | None
    cost_known_requests: int
    simulated_requests: int
    model_distribution: dict[str, int]


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
