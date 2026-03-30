
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator
from sqlalchemy import Date


class HabitOut(BaseModel):
    id: int
    slug: str
    label: str
    desc: str
    why: str
    impact: str
    category: str
    tier: str
    has_counter: bool
    unit: Optional[str] = None
    target: Optional[int] = None
    model_config = {"from_attributes": True}
 
 
class ChallengeCreate(BaseModel):
    pack_id: Optional[str] = None
    habit_slugs: list[str]
 
    @field_validator("habit_slugs")
    @classmethod
    def validate_habits(cls, v):
        if not 2 <= len(v) <= 6:
            raise ValueError("Choose between 2 and 6 habits")
        return v
 
 
class ChallengeOut(BaseModel):
    id: int
    pack_id: Optional[str]
    status: str
    started_at: date
    ends_at: date
    habits: list[HabitOut] = []
    model_config = {"from_attributes": True}
 
 
class LogCreate(BaseModel):
    commitment_id: int
    logged_date: date
    completed: bool = True
    value: Optional[int] = None
 
 
class LogOut(BaseModel):
    id: int
    commitment_id: int
    logged_date: Date
    completed: bool
    value: Optional[int]
    logged_at: datetime
    model_config = {"from_attributes": True}
 
 
class HabitTodayOut(BaseModel):
    commitment_id: int
    habit: HabitOut
    completed: bool
    value: Optional[int]
    log_id: Optional[int]
 
 
class TodayOut(BaseModel):
    challenge_id: int
    date: Date
    day_number: int
    habits: list[HabitTodayOut]
    completed_count: int
    total_count: int
 
 
class StreakOut(BaseModel):
    challenge_id: int
    current_streak: int
    longest_streak: int
    perfect_days: int
    completion_pct: float