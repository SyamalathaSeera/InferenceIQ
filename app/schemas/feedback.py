from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    receipt_id: str = Field(min_length=4, max_length=64)
    score: float = Field(ge=0, le=1)
    outcome: str = Field(min_length=1, max_length=100)


class FeedbackResponse(BaseModel):
    id: str
    receipt_id: str
    score: float
    outcome: str
    created_at: datetime
