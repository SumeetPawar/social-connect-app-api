
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class HabitOut(BaseModel):
    id: int
    slug: str
    label: str
    description: str
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
    logged_date: date
    completed: bool
    value: Optional[int]
    logged_at: datetime
    model_config = {"from_attributes": True}


class LogWithStreakOut(BaseModel):
    # Log fields
    id: int
    commitment_id: int
    logged_date: date
    completed: bool
    value: Optional[int]
    logged_at: datetime
    # Streak fields
    challenge_id: int
    current_streak: int
    effective_streak: int
    longest_streak: int
    perfect_days: int
    completion_pct: float
    shields_earned: int
    shields_used: int
    shield_used_on_dates: list[date]
 
 
class HabitTodayOut(BaseModel):
    commitment_id: int
    habit: HabitOut
    completed: bool
    value: Optional[int]
    log_id: Optional[int]
 
 
class TodayOut(BaseModel):
    challenge_id: int
    date: date
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
    shields_earned: int
    shields_used: int
    effective_streak: int  # streak after shield protection
    shield_used_on_dates: list[date]  # dates where shield was consumed


class LeaderboardEntry(BaseModel):
    rank: int
    rank_change: int          # positive = moved up, negative = moved down, 0 = same
    user_id: str
    name: Optional[str]
    profile_pic_url: Optional[str]
    challenge_id: int
    completion_pct: float
    completed: int            # habits completed in period
    possible: int             # total possible (habits × days)
    streak: int               # current perfect-day streak


class LeaderboardOut(BaseModel):
    period_days: int
    period_start: date
    period_end: date
    entries: list[LeaderboardEntry]


class HabitHistoryEntry(BaseModel):
    commitment_id: int
    habit: HabitOut
    days_completed: int
    days_total: int
    completion_pct: float


class DailyHabitStatus(BaseModel):
    commitment_id: int
    habit_slug: str
    habit_label: str
    completed: bool
    value: Optional[int] = None


class DayEntry(BaseModel):
    date: date
    day_number: int
    habits: list[DailyHabitStatus]
    all_completed: bool
    completed_count: int
    total_count: int


class ChallengeHistoryOut(BaseModel):
    id: int
    pack_id: Optional[str]
    status: str
    started_at: date
    ends_at: date
    total_days: int
    days_elapsed: int
    perfect_days: int
    completion_pct: float
    current_streak: int
    longest_streak: int
    shields_earned: int
    shields_used: int
    effective_streak: int
    shield_used_on_dates: list[date]
    habits: list[HabitHistoryEntry]
    daily_logs: list[DayEntry]
    model_config = {"from_attributes": True}