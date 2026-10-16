from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import require_admin
from app.database import get_session
from app.inference.simulator import simulate
from app.persistence.repository import load_simulation_samples
from app.schemas.simulation import SimulationRequest, SimulationResponse

router = APIRouter(prefix="/v1", tags=["simulations"])


@router.post("/simulations", response_model=SimulationResponse)
async def create_simulation(
    body: SimulationRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_key: Annotated[str | None, Header(alias="X-InferenceIQ-Admin-Key")] = None,
) -> SimulationResponse:
    require_admin(request, admin_key)
    samples = await load_simulation_samples(session, limit=body.limit)
    return simulate(samples, body)
