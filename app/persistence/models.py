from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class InferenceRecord(Base):
    __tablename__ = "inference_records"

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    requested_model: Mapped[str | None] = mapped_column(String(255))
    selected_model: Mapped[str] = mapped_column(String(255), index=True)
    selected_provider: Mapped[str] = mapped_column(String(100), index=True)
    policy: Mapped[str] = mapped_column(String(50), index=True)
    predicted_input_tokens: Mapped[int | None] = mapped_column(Integer)
    assumed_output_tokens: Mapped[int | None] = mapped_column(Integer)
    predicted_cost_usd: Mapped[float | None] = mapped_column(Float)
    actual_input_tokens: Mapped[int | None] = mapped_column(Integer)
    actual_output_tokens: Mapped[int | None] = mapped_column(Integer)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float)
    provider_latency_ms: Mapped[float | None] = mapped_column(Float)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    fallbacks: Mapped[list] = mapped_column(JSON, default=list)
    langsmith_trace_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_records.id"), nullable=True, index=True
    )
    rollout_group: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    pii_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pii_detected_types: Mapped[list] = mapped_column(JSON, default=list)
    pii_redaction_count: Mapped[int] = mapped_column(Integer, default=0)


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"fb_{uuid4().hex}"
    )
    receipt_id: Mapped[str] = mapped_column(
        ForeignKey("inference_records.receipt_id"), index=True
    )
    score: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PolicyRecord(Base):
    __tablename__ = "policy_records"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"pol_{uuid4().hex}"
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(String(50))
    require_zero_data_retention: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), index=True)
    sample_limit: Mapped[int] = mapped_column(Integer)
    simulation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canary_percent: Mapped[int] = mapped_column(Integer, default=10)
    guardrail_min_requests: Mapped[int] = mapped_column(Integer, default=20)
    max_error_rate: Mapped[float] = mapped_column(Float, default=0.1)
    max_latency_increase_percent: Mapped[float] = mapped_column(Float, default=30.0)
    max_cost_increase_percent: Mapped[float] = mapped_column(Float, default=30.0)
    rollback_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RolloutEvent(Base):
    __tablename__ = "rollout_events"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"re_{uuid4().hex}"
    )
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy_records.id"), index=True)
    receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollout_group: Mapped[str] = mapped_column(String(20), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"prj_{uuid4().hex}"
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=60)
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pii_policy: Mapped[str] = mapped_column(String(20), default="redact")
    audit_sequence: Mapped[int] = mapped_column(Integer, default=0)
    audit_chain_head: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ApiKeyRecord(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"key_{uuid4().hex}"
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class RequestAdmission(Base):
    __tablename__ = "request_admissions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"adm_{uuid4().hex}"
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("project_id", "sequence"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"aud_{uuid4().hex}"
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)


class ProviderHealthRecord(Base):
    __tablename__ = "provider_health"

    model_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    circuit_state: Mapped[str] = mapped_column(String(20), default="closed", index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    total_successes: Mapped[int] = mapped_column(Integer, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, default=0)
    failure_ewma: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ewma_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
