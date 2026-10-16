from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import require_admin
from app.database import get_session
from app.health import (
    health_score,
    list_health_records,
    load_model_health,
    reset_circuit,
)
from app.schemas.health import (
    CircuitResetRequest,
    CircuitResetResponse,
    ProviderHealthResponse,
)

router = APIRouter(prefix="/v1/provider-health", tags=["provider health"])


@router.get("", response_model=list[ProviderHealthResponse])
async def read_provider_health(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> list[ProviderHealthResponse]:
    require_admin(request, admin_key)
    await load_model_health(
        session,
        cooldown_seconds=request.app.state.settings.circuit_cooldown_seconds,
    )
    records = await list_health_records(session)
    return [
        ProviderHealthResponse(
            model_id=record.model_id,
            provider=record.provider,
            circuit_state=record.circuit_state,
            health_score=health_score(record),
            consecutive_failures=record.consecutive_failures,
            total_successes=record.total_successes,
            total_failures=record.total_failures,
            failure_ewma=round(record.failure_ewma, 4),
            latency_ewma_ms=round(record.latency_ewma_ms, 2)
            if record.latency_ewma_ms is not None
            else None,
            last_error_category=record.last_error_category,
            opened_at=record.opened_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]


@router.post("/reset", response_model=CircuitResetResponse)
async def reset_provider_circuit(
    body: CircuitResetRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> CircuitResetResponse:
    require_admin(request, admin_key)
    if not await reset_circuit(session, body.model_id):
        raise HTTPException(status_code=404, detail="Provider health record not found")
    return CircuitResetResponse(model_id=body.model_id)
