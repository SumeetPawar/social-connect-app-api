from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User, DailySteps, ChallengeParticipant, Challenge
from app.schemas.steps import (
    StepsAddRequest,
    StepsAddResponse,
    DayTotalResponse,
    WeekProgressResponse,
    WeekProgressDay
)

router = APIRouter(prefix="/api/steps", tags=["Steps"])


@router.post("/add", response_model=StepsAddResponse)
async def add_steps(
    payload: StepsAddRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add steps for a specific date.
    Creates or updates the daily_steps record.
    """
    log_date = payload.day or date.today()
    
    # Get or create daily steps record
    stmt = select(DailySteps).where(
        and_(
            DailySteps.user_id == current_user.id,
            DailySteps.day == log_date
        )
    )
    result = await db.execute(stmt)
    daily_steps = result.scalar_one_or_none()
    
    if daily_steps:
        daily_steps.steps = payload.steps
    else:
        # Create new
        daily_steps = DailySteps(
            user_id=str(current_user.id),
            day=payload.day,
            steps=payload.steps
        )
        db.add(daily_steps)
    
    await db.commit()
    await db.refresh(daily_steps)
    
    return {
        "log_id": f"{daily_steps.user_id}_{daily_steps.day}", #remove if not needed
        "day": daily_steps.day,
        "added_steps": payload.steps,
        "day_total": daily_steps.steps
    }


@router.get("/weekly", response_model=WeekProgressResponse)
async def get_weekly_steps(
    week_start: Optional[date] = Query(None, description="Start of week (Monday). Defaults to current week."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get steps for the entire week (Monday to Sunday).
    Includes daily breakdown and weekly total.
    """
    # Calculate week boundaries
    if not week_start:
        today = date.today()
        # Get Monday of current week
        week_start = today - timedelta(days=today.weekday())
    
    period_start = week_start
    period_end = week_start + timedelta(days=6)  # Sunday
    
    # Get user's active challenge and goal
    tz = ZoneInfo(current_user.timezone or "Asia/Kolkata")
    today = datetime.now(tz).date()
    
    # Get active challenge participation
    challenge_stmt = select(Challenge, ChallengeParticipant).join(
        ChallengeParticipant,
        ChallengeParticipant.challenge_id == Challenge.id
    ).where(
        ChallengeParticipant.user_id == current_user.id,
        ChallengeParticipant.left_at.is_(None),
        Challenge.status == 'active',
        Challenge.start_date <= today,
        Challenge.end_date >= today,
    )
    challenge_result = await db.execute(challenge_stmt)
    challenge_data = challenge_result.first()
    
    daily_target = 5000.0  # Default
    period_target = 35000.0  # Default weekly
    
    if challenge_data:
        challenge, participant = challenge_data
        if participant.selected_daily_target:
            daily_target = float(participant.selected_daily_target)
            # Calculate period_target based on join date
            join_date = participant.joined_at.date() if hasattr(participant.joined_at, 'date') else participant.joined_at
            # If joined this week, use days left
            if join_date > period_start:
                days_left = (period_end - join_date).days + 1
                days_left = max(days_left, 0)
                period_target = daily_target * days_left
            else:
                period_target = daily_target * 7
    
    # Get steps for the week
    steps_stmt = select(DailySteps).where(
        and_(
            DailySteps.user_id == current_user.id,
            DailySteps.day >= period_start,
            DailySteps.day <= period_end
        )
    ).order_by(DailySteps.day)
    
    steps_result = await db.execute(steps_stmt)
    steps_records = steps_result.scalars().all()
    
    # Create a map of date -> steps
    steps_map = {record.day: record.steps for record in steps_records}

    
    # Build days array (always 7 days)
    days = []
    week_total = 0
    
    for i in range(7):
        day_date = period_start + timedelta(days=i)
        day_steps = steps_map.get(day_date, 0)
        week_total += day_steps
        
        days.append({
            "day": day_date,
            "total_steps": day_steps
        })
    
    # Calculate progress
    progress_pct = (week_total / period_target * 100) if period_target > 0 else 0
    remaining_steps = max(period_target - week_total, 0)
    
    return {
        "anchor_start": week_start,
        "period_start": period_start,
        "period_end": period_end,
        "goal_daily_target": daily_target,
        "goal_period_target": period_target,
        "week_total_steps": week_total,
        "progress_pct": round(progress_pct, 2),
        "remaining_steps": remaining_steps,
        "days": days
    }


@router.get("/day/{log_date}", response_model=DayTotalResponse)
async def get_day_total(
    log_date: date,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get total steps for a specific day.
    """
    stmt = select(DailySteps).where(
        and_(
            DailySteps.user_id == current_user.id,
            DailySteps.day == log_date
        )
    )
    result = await db.execute(stmt)
    daily_steps = result.scalar_one_or_none()
    
    return {
        "day": log_date,
        "total_steps": daily_steps.steps if daily_steps else 0
    }


@router.get("/history")
async def get_steps_history(
    days: int = Query(30, description="Number of days to retrieve"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get steps history for the last N days.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    
    stmt = select(DailySteps).where(
        and_(
            DailySteps.user_id == current_user.id,
            DailySteps.day >= start_date,
            DailySteps.day <= end_date
        )
    ).order_by(DailySteps.day.desc())
    
    result = await db.execute(stmt)
    records = result.scalars().all()
    
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_days": days,
        "records": [
            {
                "day": r.day,
                "steps": r.steps
            }
            for r in records
        ]
    }