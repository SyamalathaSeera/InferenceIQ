import hashlib
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.inference.models import InferenceOptions, InferenceReceipt
from app.persistence.models import PolicyRecord, RolloutEvent
from app.persistence.repository import (
    get_active_canary,
    get_policy,
    load_rollout_events,
    save_rollout_event,
)
from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True)
class RolloutAssignment:
    policy_id: str
    rollout_group: str
    options: InferenceOptions


@dataclass(frozen=True)
class GroupSummary:
    requests: int
    errors: int
    error_rate: float | None
    average_latency_ms: float | None
    average_cost_usd: float | None


def client_selected_policy(request: ChatCompletionRequest) -> bool:
    return (
        "inferenceiq" in request.model_fields_set
        and "policy" in request.inferenceiq.model_fields_set
    )


async def assign_rollout(
    session: AsyncSession,
    request: ChatCompletionRequest,
) -> RolloutAssignment | None:
    """Assign implicit, non-streaming automatic requests to control or canary."""

    if request.model != "auto" or request.stream or client_selected_policy(request):
        return None
    policy = await get_active_canary(session)
    if policy is None:
        return None

    fingerprint = hashlib.sha256(
        f"{policy.id}:{_request_identity(request)}".encode()
    ).hexdigest()
    bucket = int(fingerprint[:8], 16) % 10_000
    is_canary = bucket < policy.canary_percent * 100
    options = request.inferenceiq
    if is_canary:
        options = options.model_copy(
            update={
                "policy": policy.policy,
                "require_zero_data_retention": (
                    options.require_zero_data_retention
                    or policy.require_zero_data_retention
                ),
            }
        )
    return RolloutAssignment(
        policy_id=policy.id,
        rollout_group="canary" if is_canary else "control",
        options=options,
    )


def apply_assignment(
    request: ChatCompletionRequest,
    assignment: RolloutAssignment | None,
) -> ChatCompletionRequest:
    if assignment is None:
        return request
    return request.model_copy(update={"inferenceiq": assignment.options})


def attach_assignment(
    receipt: InferenceReceipt,
    assignment: RolloutAssignment | None,
) -> InferenceReceipt:
    if assignment is None:
        return receipt
    return receipt.model_copy(
        update={
            "policy_version_id": assignment.policy_id,
            "rollout_group": assignment.rollout_group,
        }
    )


async def record_outcome_and_apply_guardrails(
    session: AsyncSession,
    assignment: RolloutAssignment | None,
    *,
    receipt_id: str | None,
    success: bool,
    latency_ms: float | None,
    cost_usd: float | None,
) -> PolicyRecord | None:
    if assignment is None:
        return None
    await save_rollout_event(
        session,
        policy_id=assignment.policy_id,
        receipt_id=receipt_id,
        rollout_group=assignment.rollout_group,
        success=success,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
    policy = await get_policy(session, assignment.policy_id)
    if policy is None or policy.status != "canary":
        return policy

    events = await load_rollout_events(session, policy.id)
    control = summarize_group(events, "control")
    canary = summarize_group(events, "canary")
    if canary.requests < policy.guardrail_min_requests:
        return policy

    violations: list[str] = []
    if canary.error_rate is not None and canary.error_rate > policy.max_error_rate:
        violations.append(
            f"canary error rate {canary.error_rate:.2%} exceeded "
            f"{policy.max_error_rate:.2%}"
        )
    _compare_metric(
        violations,
        label="latency",
        control=control.average_latency_ms,
        canary=canary.average_latency_ms,
        maximum_increase=policy.max_latency_increase_percent,
    )
    _compare_metric(
        violations,
        label="cost",
        control=control.average_cost_usd,
        canary=canary.average_cost_usd,
        maximum_increase=policy.max_cost_increase_percent,
    )
    if violations:
        policy.status = "rolled_back"
        policy.rollback_reason = "; ".join(violations)
        await session.commit()
        await session.refresh(policy)
    return policy


def summarize_group(events: list[RolloutEvent], group: str) -> GroupSummary:
    selected = [event for event in events if event.rollout_group == group]
    errors = sum(not event.success for event in selected)
    latencies = [event.latency_ms for event in selected if event.latency_ms is not None]
    costs = [event.cost_usd for event in selected if event.cost_usd is not None]
    return GroupSummary(
        requests=len(selected),
        errors=errors,
        error_rate=round(errors / len(selected), 4) if selected else None,
        average_latency_ms=round(sum(latencies) / len(latencies), 2)
        if latencies
        else None,
        average_cost_usd=round(sum(costs) / len(costs), 8) if costs else None,
    )


def _request_identity(request: ChatCompletionRequest) -> str:
    content = "|".join(
        f"{message.role}:{message.content or ''}" for message in request.messages
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _compare_metric(
    violations: list[str],
    *,
    label: str,
    control: float | None,
    canary: float | None,
    maximum_increase: float,
) -> None:
    if control is None or canary is None or control <= 0:
        return
    increase = (canary - control) / control * 100
    if increase > maximum_increase:
        violations.append(
            f"canary {label} increase {increase:.2f}% exceeded {maximum_increase:.2f}%"
        )
