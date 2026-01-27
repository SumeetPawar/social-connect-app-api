from pydantic import BaseModel
from datetime import date
from typing import Optional

class StepsAddRequest(BaseModel):
    steps: int
    day: Optional[date] = None
    source: Optional[str] = "manual"
    note: Optional[str] = None

class StepsAddResponse(BaseModel):
    log_id: Optional[str] = None # Optional, can be removed if not needed
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