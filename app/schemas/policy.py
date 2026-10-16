from typing import Literal

from pydantic import BaseModel, Field

from app.inference.models import PolicyName
from app.schemas.simulation import SimulationResponse

PolicyStatus = Literal[
    "draft",
    "invalid",
    "pending_approval",
    "canary",
    "rejected",
    "rolled_back",
]


class PolicyProposalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    policy: PolicyName
    require_zero_data_retention: bool = False
    sample_limit: int = Field(default=100, ge=1, le=1000)
    canary_percent: int = Field(default=10, ge=1, le=100)
    guardrail_min_requests: int = Field(default=20, ge=1, le=10000)
    max_error_rate: float = Field(default=0.1, ge=0, le=1)
    max_latency_increase_percent: float = Field(default=30, ge=0, le=1000)
    max_cost_increase_percent: float = Field(default=30, ge=0, le=1000)


class PolicyReviewRequest(BaseModel):
    approved: bool
    reviewed_by: str = Field(min_length=1, max_length=100)


class PolicyWorkflowResponse(BaseModel):
    id: str
    name: str
    version: int
    policy: PolicyName
    require_zero_data_retention: bool
    status: PolicyStatus
    review_required: bool
    simulation: SimulationResponse | None
    validation_errors: list[str]
    reviewed_by: str | None
    canary_percent: int
    guardrail_min_requests: int
    max_error_rate: float
    max_latency_increase_percent: float
    max_cost_increase_percent: float
    rollback_reason: str | None


class RolloutGroupMetrics(BaseModel):
    requests: int
    errors: int
    error_rate: float | None
    average_latency_ms: float | None
    average_cost_usd: float | None


class RolloutMetricsResponse(BaseModel):
    policy_id: str
    status: PolicyStatus
    control: RolloutGroupMetrics
    canary: RolloutGroupMetrics
    rollback_reason: str | None
