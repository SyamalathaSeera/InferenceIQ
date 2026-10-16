from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProviderHealthResponse(BaseModel):
    model_id: str
    provider: str
    circuit_state: Literal["closed", "open", "half_open"]
    health_score: float
    consecutive_failures: int
    total_successes: int
    total_failures: int
    failure_ewma: float
    latency_ewma_ms: float | None
    last_error_category: str | None
    opened_at: datetime | None
    updated_at: datetime


class CircuitResetRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=255)


class CircuitResetResponse(BaseModel):
    model_id: str
    circuit_state: Literal["closed"] = "closed"
