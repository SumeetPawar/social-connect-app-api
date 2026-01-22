from pydantic import BaseModel, Field
from datetime import date
from typing import Literal
from uuid import UUID 

class GoalSetRequest(BaseModel):
    metric_key: str = Field(default="steps")
    period: Literal["week", "month"] = "week"
    daily_target: float = Field(..., gt=0)  # NUMERIC(12,2)

class GoalSetResponse(BaseModel):
    id: UUID
    user_id: UUID
    metric_key: str
    period: str
    daily_target: float
    period_target: float
    period_start: date
    period_end: date
    anchor_start: date
