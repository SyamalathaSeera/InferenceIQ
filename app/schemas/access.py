from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PiiPolicy = Literal["off", "detect", "redact", "block"]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    rate_limit_rpm: int = Field(default=60, ge=1, le=100000)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    pii_policy: PiiPolicy = "redact"


class ProjectResponse(BaseModel):
    id: str
    name: str
    enabled: bool
    rate_limit_rpm: int
    monthly_budget_usd: float | None
    pii_policy: PiiPolicy
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyCreatedResponse(BaseModel):
    id: str
    project_id: str
    name: str
    key_prefix: str
    secret: str
    created_at: datetime


class ApiKeyResponse(BaseModel):
    id: str
    project_id: str
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ProjectUsageResponse(BaseModel):
    project_id: str
    month: str
    admitted_requests: int
    completed_requests: int
    input_tokens: int
    output_tokens: int
    spend_usd: float
    unknown_cost_requests: int
    monthly_budget_usd: float | None
    budget_remaining_usd: float | None


class AuditEventResponse(BaseModel):
    id: str
    sequence: int
    event_type: str
    actor_type: str
    actor_id: str | None
    details: dict
    occurred_at: datetime
    previous_hash: str | None
    event_hash: str


class AuditIntegrityResponse(BaseModel):
    project_id: str
    valid: bool
    event_count: int
    first_invalid_sequence: int | None
    chain_head: str | None


class InferenceSummaryResponse(BaseModel):
    receipt_id: str
    created_at: datetime
    requested_model: str | None
    selected_model: str
    selected_provider: str
    policy: str
    actual_input_tokens: int | None
    actual_output_tokens: int | None
    actual_cost_usd: float | None
    provider_latency_ms: float | None
    rollout_group: str | None
    policy_version_id: str | None
    pii_action: str | None
    pii_detected_types: list[str]
