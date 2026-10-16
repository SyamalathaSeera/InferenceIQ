from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import authenticate_without_admission
from app.database import get_session
from app.observability import LangSmithObserver
from app.persistence.repository import (
    get_inference_project_id,
    get_inference_trace_id,
    save_feedback,
)
from app.schemas.feedback import FeedbackRequest, FeedbackResponse

router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_feedback(
    body: FeedbackRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> FeedbackResponse:
    access = await authenticate_without_admission(session, request)
    if access is not None:
        project_id = await get_inference_project_id(session, body.receipt_id)
        if project_id != access.project.id:
            raise HTTPException(
                status_code=404, detail="Inference receipt was not found."
            )
    record = await save_feedback(session, body)
    if record is None:
        raise HTTPException(status_code=404, detail="Inference receipt was not found.")
    observer: LangSmithObserver = request.app.state.observer
    trace_id = await get_inference_trace_id(session, body.receipt_id)
    await observer.record_feedback(
        trace_id=trace_id,
        score=body.score,
        outcome=body.outcome,
    )
    return FeedbackResponse.model_validate(record, from_attributes=True)
