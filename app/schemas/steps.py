from pydantic import BaseModel, ConfigDict, Field
from datetime import date
from uuid import UUID
from typing import Literal, Optional

class StepsAddRequest(BaseModel):
    steps: int = Field(..., gt=0)
    log_date: Optional[date] = None  # default: today in user's timezone
    source: str = "manual"
    note: Optional[str] = None

class StepsAddResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: UUID
    day: date
    added_steps: int
    day_total: int

class DayTotalResponse(BaseModel):
    day: date
    total_steps: int

class WeekProgressDay(BaseModel):
    day: date
    total_steps: int

class WeekProgressResponse(BaseModel):
    anchor_start: date
    period_start: date
    period_end: date

    goal_daily_target: float
    goal_period_target: float

    week_total_steps: int
    progress_pct: float
    remaining_steps: float

    days: list[WeekProgressDay]
