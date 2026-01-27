from pydantic import BaseModel, Field
from datetime import date

class SetDailyTargetRequest(BaseModel):
    daily_target: int = Field(..., description="Daily step target (3000, 5000, 7500, or 10000)")

class SetDailyTargetResponse(BaseModel):
    challenge_id: str
    challenge_title: str
    daily_target: int
    weekly_target: int
    challenge_start: date
    challenge_end: date

class CurrentGoalResponse(BaseModel):
    challenge_id: str
    challenge_title: str
    daily_target: int
    weekly_target: int
    challenge_start: date
    challenge_end: date
    has_target_set: bool