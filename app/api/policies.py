from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import require_admin
from app.database import get_session
from app.persistence.models import PolicyRecord
from app.persistence.repository import (
    create_policy,
    get_active_canary,
    get_policy,
    list_policies,
    load_rollout_events,
    load_simulation_samples,
    update_policy_workflow,
)
from app.policy.rollout import summarize_group
from app.schemas.policy import (
    PolicyProposalRequest,
    PolicyReviewRequest,
    PolicyWorkflowResponse,
    RolloutGroupMetrics,
    RolloutMetricsResponse,
)

router = APIRouter(prefix="/v1/policy-workflows", tags=["policy workflows"])


def _response(record: PolicyRecord) -> PolicyWorkflowResponse:
    return PolicyWorkflowResponse(
        id=record.id,
        name=record.name,
        version=record.version,
        policy=record.policy,
        require_zero_data_retention=record.require_zero_data_retention,
        status=record.status,
        review_required=record.status == "pending_approval",
        simulation=record.simulation,
        validation_errors=record.validation_errors,
        reviewed_by=record.reviewed_by,
        canary_percent=record.canary_percent,
        guardrail_min_requests=record.guardrail_min_requests,
        max_error_rate=record.max_error_rate,
        max_latency_increase_percent=record.max_latency_increase_percent,
        max_cost_increase_percent=record.max_cost_increase_percent,
        rollback_reason=record.rollback_reason,
    )


@router.get("", response_model=list[PolicyWorkflowResponse])
async def read_policies(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> list[PolicyWorkflowResponse]:
    require_admin(request, admin_key)
    return [_response(record) for record in await list_policies(session)]


@router.post(
    "", response_model=PolicyWorkflowResponse, status_code=status.HTTP_201_CREATED
)
async def propose_policy(
    body: PolicyProposalRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> PolicyWorkflowResponse:
    require_admin(request, admin_key)
    record = await create_policy(
        session,
        name=body.name,
        policy=body.policy,
        require_zero_data_retention=body.require_zero_data_retention,
        sample_limit=body.sample_limit,
        canary_percent=body.canary_percent,
        guardrail_min_requests=body.guardrail_min_requests,
        max_error_rate=body.max_error_rate,
        max_latency_increase_percent=body.max_latency_increase_percent,
        max_cost_increase_percent=body.max_cost_increase_percent,
    )
    samples = await load_simulation_samples(session, limit=body.sample_limit)
    result: dict[str, Any] = await request.app.state.policy_graph.ainvoke(
        {
            "policy_id": record.id,
            "name": record.name,
            "version": record.version,
            "policy": record.policy,
            "require_zero_data_retention": record.require_zero_data_retention,
            "samples": [asdict(sample) for sample in samples],
        },
        {"configurable": {"thread_id": record.id}},
    )
    await update_policy_workflow(
        session,
        record,
        status=result["status"],
        simulation=result.get("simulation"),
        validation_errors=result.get("validation_errors", []),
    )
    return _response(record)


@router.get("/{policy_id}", response_model=PolicyWorkflowResponse)
async def read_policy(
    policy_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> PolicyWorkflowResponse:
    require_admin(request, admin_key)
    record = await get_policy(session, policy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Policy workflow not found")
    return _response(record)


@router.post("/{policy_id}/review", response_model=PolicyWorkflowResponse)
async def review_policy(
    policy_id: str,
    body: PolicyReviewRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> PolicyWorkflowResponse:
    require_admin(request, admin_key)
    record = await get_policy(session, policy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Policy workflow not found")
    if record.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Policy cannot be reviewed while status is '{record.status}'",
        )
    if (
        body.approved
        and await get_active_canary(session, exclude_policy_id=record.id) is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="Another policy canary is already active",
        )

    result: dict[str, Any] = await request.app.state.policy_graph.ainvoke(
        Command(resume=body.approved),
        {"configurable": {"thread_id": record.id}},
    )
    await update_policy_workflow(
        session,
        record,
        status=result["status"],
        reviewed_by=body.reviewed_by,
    )
    return _response(record)


def _group_metrics(events: list, group: str) -> RolloutGroupMetrics:
    summary = summarize_group(events, group)
    return RolloutGroupMetrics(
        requests=summary.requests,
        errors=summary.errors,
        error_rate=summary.error_rate,
        average_latency_ms=summary.average_latency_ms,
        average_cost_usd=summary.average_cost_usd,
    )


@router.get("/{policy_id}/rollout-metrics", response_model=RolloutMetricsResponse)
async def read_rollout_metrics(
    policy_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> RolloutMetricsResponse:
    require_admin(request, admin_key)
    record = await get_policy(session, policy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Policy workflow not found")
    events = await load_rollout_events(session, policy_id)
    return RolloutMetricsResponse(
        policy_id=record.id,
        status=record.status,
        control=_group_metrics(events, "control"),
        canary=_group_metrics(events, "canary"),
        rollback_reason=record.rollback_reason,
    )
