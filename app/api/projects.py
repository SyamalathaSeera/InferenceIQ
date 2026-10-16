from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    create_project,
    get_project,
    issue_api_key,
    list_project_inferences,
    list_projects,
    load_project_usage,
    require_admin,
    revoke_api_key,
)
from app.audit import append_audit_event, load_audit_events, verify_audit_chain
from app.database import get_session
from app.persistence.models import ApiKeyRecord, Project
from app.schemas.access import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    AuditEventResponse,
    AuditIntegrityResponse,
    InferenceSummaryResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUsageResponse,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        enabled=project.enabled,
        rate_limit_rpm=project.rate_limit_rpm,
        monthly_budget_usd=project.monthly_budget_usd,
        pii_policy=project.pii_policy,
        created_at=project.created_at,
    )


def _key_response(key: ApiKeyRecord) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        revoked_at=key.revoked_at,
    )


@router.get("", response_model=list[ProjectResponse])
async def read_projects(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> list[ProjectResponse]:
    require_admin(request, admin_key)
    return [_project_response(project) for project in await list_projects(session)]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    body: ProjectCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> ProjectResponse:
    require_admin(request, admin_key)
    project = await create_project(
        session,
        name=body.name,
        rate_limit_rpm=body.rate_limit_rpm,
        monthly_budget_usd=body.monthly_budget_usd,
        pii_policy=body.pii_policy,
    )
    await append_audit_event(
        session,
        signing_key=request.app.state.settings.inferenceiq_audit_signing_key,
        project_id=project.id,
        event_type="project.created",
        actor_type="admin",
        actor_id=None,
        details={
            "rate_limit_rpm": project.rate_limit_rpm,
            "monthly_budget_usd": project.monthly_budget_usd,
            "pii_policy": project.pii_policy,
        },
    )
    return _project_response(project)


@router.post(
    "/{project_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key_endpoint(
    project_id: str,
    body: ApiKeyCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> ApiKeyCreatedResponse:
    require_admin(request, admin_key)
    key, secret = await issue_api_key(session, project_id=project_id, name=body.name)
    await append_audit_event(
        session,
        signing_key=request.app.state.settings.inferenceiq_audit_signing_key,
        project_id=project_id,
        event_type="api_key.created",
        actor_type="admin",
        actor_id=None,
        details={"key_id": key.id, "key_prefix": key.key_prefix, "name": key.name},
    )
    return ApiKeyCreatedResponse(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        key_prefix=key.key_prefix,
        secret=secret,
        created_at=key.created_at,
    )


@router.post("/{project_id}/api-keys/{key_id}/revoke", response_model=ApiKeyResponse)
async def revoke_api_key_endpoint(
    project_id: str,
    key_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> ApiKeyResponse:
    require_admin(request, admin_key)
    key = await revoke_api_key(session, project_id=project_id, key_id=key_id)
    await append_audit_event(
        session,
        signing_key=request.app.state.settings.inferenceiq_audit_signing_key,
        project_id=project_id,
        event_type="api_key.revoked",
        actor_type="admin",
        actor_id=None,
        details={"key_id": key.id, "key_prefix": key.key_prefix},
    )
    return _key_response(key)


@router.get("/{project_id}/usage", response_model=ProjectUsageResponse)
async def read_project_usage(
    project_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> ProjectUsageResponse:
    require_admin(request, admin_key)
    project = await get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    usage = await load_project_usage(session, project.id)
    remaining = (
        None
        if project.monthly_budget_usd is None
        else max(0.0, round(project.monthly_budget_usd - usage.spend_usd, 8))
    )
    return ProjectUsageResponse(
        project_id=project.id,
        month=datetime_month(),
        admitted_requests=usage.admitted_requests,
        completed_requests=usage.completed_requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        spend_usd=usage.spend_usd,
        unknown_cost_requests=usage.unknown_cost_requests,
        monthly_budget_usd=project.monthly_budget_usd,
        budget_remaining_usd=remaining,
    )


def datetime_month() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m")


@router.get(
    "/{project_id}/inferences",
    response_model=list[InferenceSummaryResponse],
)
async def read_project_inferences(
    project_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> list[InferenceSummaryResponse]:
    require_admin(request, admin_key)
    if await get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    records = await list_project_inferences(session, project_id, limit=limit)
    return [
        InferenceSummaryResponse(
            receipt_id=record.receipt_id,
            created_at=record.created_at,
            requested_model=record.requested_model,
            selected_model=record.selected_model,
            selected_provider=record.selected_provider,
            policy=record.policy,
            actual_input_tokens=record.actual_input_tokens,
            actual_output_tokens=record.actual_output_tokens,
            actual_cost_usd=record.actual_cost_usd,
            provider_latency_ms=record.provider_latency_ms,
            rollout_group=record.rollout_group,
            policy_version_id=record.policy_version_id,
            pii_action=record.pii_action,
            pii_detected_types=record.pii_detected_types,
        )
        for record in records
    ]


@router.get("/{project_id}/audit-events", response_model=list[AuditEventResponse])
async def read_audit_events(
    project_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> list[AuditEventResponse]:
    require_admin(request, admin_key)
    if await get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    events = await load_audit_events(session, project_id, limit=limit)
    return [
        AuditEventResponse(
            id=event.id,
            sequence=event.sequence,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            details=event.details,
            occurred_at=event.occurred_at,
            previous_hash=event.previous_hash,
            event_hash=event.event_hash,
        )
        for event in events
    ]


@router.get("/{project_id}/audit-integrity", response_model=AuditIntegrityResponse)
async def read_audit_integrity(
    project_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> AuditIntegrityResponse:
    require_admin(request, admin_key)
    if await get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    integrity = await verify_audit_chain(
        session,
        signing_key=request.app.state.settings.inferenceiq_audit_signing_key,
        project_id=project_id,
    )
    return AuditIntegrityResponse(
        project_id=project_id,
        valid=integrity.valid,
        event_count=integrity.event_count,
        first_invalid_sequence=integrity.first_invalid_sequence,
        chain_head=integrity.chain_head,
    )
