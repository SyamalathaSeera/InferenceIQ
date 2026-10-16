import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inference.simulator import HistoricalSample
from app.persistence.models import (
    FeedbackRecord,
    InferenceRecord,
    PolicyRecord,
    RolloutEvent,
    utc_now,
)
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.schemas.feedback import FeedbackRequest


def request_fingerprint(request: ChatCompletionRequest) -> str:
    """Hash request content so prompts need not be stored in plaintext."""

    canonical = json.dumps(
        [message.model_dump(exclude_none=True) for message in request.messages],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def save_inference(
    session: AsyncSession,
    request: ChatCompletionRequest,
    response: ChatCompletionResponse,
    *,
    langsmith_trace_id: str | None = None,
    project_id: str | None = None,
) -> InferenceRecord:
    receipt = response.inference_receipt
    if receipt is None:
        raise ValueError("A completed inference receipt is required")

    record = InferenceRecord(
        receipt_id=receipt.receipt_id,
        request_fingerprint=request_fingerprint(request),
        requested_model=request.model,
        selected_model=receipt.selected_model,
        selected_provider=receipt.selected_provider,
        policy=receipt.policy,
        predicted_input_tokens=receipt.predicted_input_tokens,
        assumed_output_tokens=receipt.assumed_output_tokens,
        predicted_cost_usd=receipt.predicted_cost_usd,
        actual_input_tokens=receipt.actual_input_tokens,
        actual_output_tokens=receipt.actual_output_tokens,
        actual_cost_usd=receipt.actual_cost_usd,
        provider_latency_ms=receipt.provider_latency_ms,
        candidates=[candidate.model_dump() for candidate in receipt.candidates],
        fallbacks=[fallback.model_dump() for fallback in receipt.fallbacks],
        langsmith_trace_id=langsmith_trace_id,
        policy_version_id=receipt.policy_version_id,
        rollout_group=receipt.rollout_group,
        project_id=project_id,
        pii_action=receipt.pii_action,
        pii_detected_types=receipt.pii_detected_types,
        pii_redaction_count=receipt.pii_redaction_count,
    )
    session.add(record)
    await session.commit()
    return record


async def get_inference_trace_id(
    session: AsyncSession,
    receipt_id: str,
) -> str | None:
    return await session.scalar(
        select(InferenceRecord.langsmith_trace_id).where(
            InferenceRecord.receipt_id == receipt_id
        )
    )


async def get_inference_project_id(
    session: AsyncSession,
    receipt_id: str,
) -> str | None:
    return await session.scalar(
        select(InferenceRecord.project_id).where(
            InferenceRecord.receipt_id == receipt_id
        )
    )


async def save_feedback(
    session: AsyncSession,
    feedback: FeedbackRequest,
) -> FeedbackRecord | None:
    inference_exists = await session.scalar(
        select(InferenceRecord.receipt_id).where(
            InferenceRecord.receipt_id == feedback.receipt_id
        )
    )
    if inference_exists is None:
        return None

    record = FeedbackRecord(
        receipt_id=feedback.receipt_id,
        score=feedback.score,
        outcome=feedback.outcome,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def load_simulation_samples(
    session: AsyncSession,
    *,
    limit: int,
) -> list[HistoricalSample]:
    records = list(
        (
            await session.scalars(
                select(InferenceRecord)
                .where(InferenceRecord.requested_model == "auto")
                .order_by(InferenceRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    if not records:
        return []

    feedback_rows = await session.execute(
        select(FeedbackRecord.receipt_id, func.avg(FeedbackRecord.score))
        .where(FeedbackRecord.receipt_id.in_([record.receipt_id for record in records]))
        .group_by(FeedbackRecord.receipt_id)
    )
    feedback_by_receipt = dict(feedback_rows.all())
    return [
        HistoricalSample(
            selected_model=record.selected_model,
            candidates=record.candidates,
            actual_input_tokens=record.actual_input_tokens,
            actual_output_tokens=record.actual_output_tokens,
            actual_cost_usd=record.actual_cost_usd,
            provider_latency_ms=record.provider_latency_ms,
            feedback_score=feedback_by_receipt.get(record.receipt_id),
        )
        for record in records
    ]


async def create_policy(
    session: AsyncSession,
    *,
    name: str,
    policy: str,
    require_zero_data_retention: bool,
    sample_limit: int,
    canary_percent: int,
    guardrail_min_requests: int,
    max_error_rate: float,
    max_latency_increase_percent: float,
    max_cost_increase_percent: float,
) -> PolicyRecord:
    latest_version = await session.scalar(
        select(func.max(PolicyRecord.version)).where(PolicyRecord.name == name)
    )
    record = PolicyRecord(
        name=name,
        version=(latest_version or 0) + 1,
        policy=policy,
        require_zero_data_retention=require_zero_data_retention,
        sample_limit=sample_limit,
        status="draft",
        canary_percent=canary_percent,
        guardrail_min_requests=guardrail_min_requests,
        max_error_rate=max_error_rate,
        max_latency_increase_percent=max_latency_increase_percent,
        max_cost_increase_percent=max_cost_increase_percent,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def get_policy(session: AsyncSession, policy_id: str) -> PolicyRecord | None:
    return await session.get(PolicyRecord, policy_id)


async def list_policies(session: AsyncSession) -> list[PolicyRecord]:
    return list(
        (
            await session.scalars(
                select(PolicyRecord).order_by(PolicyRecord.created_at.desc())
            )
        ).all()
    )


async def get_active_canary(
    session: AsyncSession,
    *,
    exclude_policy_id: str | None = None,
) -> PolicyRecord | None:
    statement = select(PolicyRecord).where(PolicyRecord.status == "canary")
    if exclude_policy_id is not None:
        statement = statement.where(PolicyRecord.id != exclude_policy_id)
    return await session.scalar(
        statement.order_by(PolicyRecord.reviewed_at.desc()).limit(1)
    )


async def update_policy_workflow(
    session: AsyncSession,
    record: PolicyRecord,
    *,
    status: str,
    simulation: dict | None = None,
    validation_errors: list[str] | None = None,
    reviewed_by: str | None = None,
) -> PolicyRecord:
    record.status = status
    if simulation is not None:
        record.simulation = simulation
    if validation_errors is not None:
        record.validation_errors = validation_errors
    if reviewed_by is not None:
        record.reviewed_by = reviewed_by
        record.reviewed_at = utc_now()
    await session.commit()
    await session.refresh(record)
    return record


async def save_rollout_event(
    session: AsyncSession,
    *,
    policy_id: str,
    receipt_id: str | None,
    rollout_group: str,
    success: bool,
    latency_ms: float | None,
    cost_usd: float | None,
) -> RolloutEvent:
    event = RolloutEvent(
        policy_id=policy_id,
        receipt_id=receipt_id,
        rollout_group=rollout_group,
        success=success,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
    session.add(event)
    await session.commit()
    return event


async def load_rollout_events(
    session: AsyncSession,
    policy_id: str,
) -> list[RolloutEvent]:
    return list(
        (
            await session.scalars(
                select(RolloutEvent)
                .where(RolloutEvent.policy_id == policy_id)
                .order_by(RolloutEvent.created_at)
            )
        ).all()
    )
