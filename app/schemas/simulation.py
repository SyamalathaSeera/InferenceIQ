from pydantic import BaseModel, Field

from app.inference.models import PolicyName


class SimulationRequest(BaseModel):
    policy: PolicyName
    require_zero_data_retention: bool = False
    limit: int = Field(default=100, ge=1, le=1000)


class SimulationMetrics(BaseModel):
    total_cost_usd: float | None
    average_latency_ms: float | None
    average_quality: float | None
    model_distribution: dict[str, int]


class SimulationDelta(BaseModel):
    cost_percent: float | None
    latency_percent: float | None
    quality_points: float | None


class SimulationResponse(BaseModel):
    policy: PolicyName
    sample_size: int
    changed_requests: int
    changed_percent: float
    current: SimulationMetrics
    simulated: SimulationMetrics
    delta: SimulationDelta
    methodology: list[str]
