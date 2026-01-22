from pydantic import BaseModel
from datetime import date
from typing import List

class StreakDay(BaseModel):
    day: date
    total_steps: int
    habit_done: bool
    goal_done: bool

class StreakResponse(BaseModel):
    metric_key: str
    today: date

    today_total: int
    habit_today_done: bool
    goal_today_done: bool

    habit_streak: int
    goal_streak: int

    last_14_days: List[StreakDay]