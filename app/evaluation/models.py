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
