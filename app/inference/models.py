from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PolicyName = Literal["balanced", "cost", "latency", "quality"]


class ModelProfile(BaseModel):
    """Comparable information used by the decision engine."""

    model_id: str
    provider: str
    quality_score: float = Field(ge=0, le=1)
    estimated_latency_ms: int = Field(gt=0)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)
    zero_data_retention: bool = False


class InferenceOptions(BaseModel):
    policy: PolicyName = "balanced"
    require_zero_data_retention: bool = False
    max_attempts: int = Field(default=2, ge=1, le=5)


class CandidateEvaluation(BaseModel):
    model: str
    provider: str
    eligible: bool
    score: float | None = None
    rejected_reason: str | None = None
    quality_score: float | None = None
    estimated_latency_ms: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    zero_data_retention: bool = False
    health_score: float = Field(default=1.0, ge=0, le=1)
    circuit_state: Literal["closed", "open", "half_open"] = "closed"
    observed_latency_ms: float | None = None
    consecutive_failures: int = 0


class FallbackAttempt(BaseModel):
    model: str
    provider: str
    error_category: str
    status_code: int
    latency_ms: float


class InferenceReceipt(BaseModel):
    receipt_id: str = Field(default_factory=lambda: f"ir_{uuid4().hex}")
    selected_model: str
    selected_provider: str
    policy: PolicyName | Literal["explicit"]
    reason: str
    candidates: list[CandidateEvaluation]
    predicted_input_tokens: int | None = None
    assumed_output_tokens: int | None = None
    predicted_cost_usd: float | None = None
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    actual_cost_usd: float | None = None
    provider_latency_ms: float | None = None
    fallbacks: list[FallbackAttempt] = Field(default_factory=list)
    policy_version_id: str | None = None
    rollout_group: Literal["control", "canary"] | None = None
    pii_action: Literal["off", "detect", "redact", "block"] | None = None
    pii_detected_types: list[str] = Field(default_factory=list)
    pii_redaction_count: int = 0


class InferenceDecision(BaseModel):
    model: ModelProfile
    ranked_models: list[ModelProfile]
    receipt: InferenceReceipt


class ModelHealth(BaseModel):
    model_id: str
    provider: str
    health_score: float = Field(default=1.0, ge=0, le=1)
    circuit_state: Literal["closed", "open", "half_open"] = "closed"
    observed_latency_ms: float | None = None
    consecutive_failures: int = 0
