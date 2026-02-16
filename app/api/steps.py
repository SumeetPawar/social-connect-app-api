from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
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

async def calculate_challenge_streak(
    user_id: str,
    challenge_id: str,
    db: AsyncSession
) -> dict:
    """
    Calculate streak from all saved data for this challenge.
    NOW SAVES TO DATABASE!
    """
    from datetime import timedelta
    
    # Get challenge dates
    challenge_query = text("""
        SELECT start_date, end_date
        FROM challenges
        WHERE id = :challenge_id
    """)
    
    result = await db.execute(challenge_query, {"challenge_id": challenge_id})
    challenge = result.mappings().first()
    
    if not challenge:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "days_logged": 0,
            "days_met_goal": 0,
            "total_days": 0,
            "completion_rate": 0
        }
    
    # Get participant's daily target
    participant_query = text("""
        SELECT selected_daily_target
        FROM challenge_participants
        WHERE challenge_id = :challenge_id 
        AND user_id = :user_id 
        AND left_at IS NULL
    """)
    
    result = await db.execute(
        participant_query, 
        {"challenge_id": challenge_id, "user_id": user_id}
    )
    participant = result.mappings().first()
    
    if not participant:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "days_logged": 0,
            "days_met_goal": 0,
            "total_days": 0,
            "completion_rate": 0
        }
    
    daily_target = participant['selected_daily_target']
    start_date = challenge['start_date']
    end_date = challenge['end_date']
    
    # Get all steps for challenge period
    steps_query = text("""
        SELECT day, steps
        FROM daily_steps
        WHERE user_id = :user_id
        AND day >= :start_date
        AND day <= :end_date
        ORDER BY day DESC
    """)
    
    result = await db.execute(
        steps_query,
        {
            "user_id": user_id,
            "start_date": start_date,
            "end_date": end_date
        }
    )
    daily_records = result.mappings().all()
    
    if not daily_records:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "days_logged": 0,
            "days_met_goal": 0,
            "total_days": (end_date - start_date).days + 1,
            "completion_rate": 0
        }
    
    # Create date lookup
    steps_by_date = {record['day']: record['steps'] for record in daily_records}
    
    # Get last logged date
    last_logged_date = max(steps_by_date.keys())
    
    # Calculate current streak (backwards from last logged)
    current_streak = 0
    current_day = last_logged_date
    
    while current_day >= start_date:
        steps = steps_by_date.get(current_day, 0)
        if steps >= daily_target:
            current_streak += 1
            current_day -= timedelta(days=1)
        else:
            break
    
    # Calculate longest streak (scan entire period)
    longest_streak = 0
    temp_streak = 0
    days_met_goal = 0
    
    scan_day = start_date
    while scan_day <= last_logged_date:
        steps = steps_by_date.get(scan_day, 0)
        
        if steps >= daily_target:
            temp_streak += 1
            days_met_goal += 1
            
            if temp_streak > longest_streak:
                longest_streak = temp_streak
        else:
            temp_streak = 0
        
        scan_day += timedelta(days=1)
    
    # ========== SAVE TO DATABASE ==========
    update_query = text("""
        UPDATE challenge_participants
        SET 
            challenge_current_streak = :current_streak,
            challenge_longest_streak = :longest_streak,
            last_activity_date = :last_activity_date
        WHERE challenge_id = :challenge_id 
        AND user_id = :user_id
    """)
    
    await db.execute(
        update_query,
        {
            "current_streak": current_streak,
            "longest_streak": max(longest_streak, current_streak),
            "last_activity_date": last_logged_date,
            "challenge_id": challenge_id,
            "user_id": user_id
        }
    )
    
    await db.commit()
    # ======================================
    
    return {
        "current_streak": current_streak,
        "longest_streak": max(longest_streak, current_streak),
        "days_logged": len(daily_records),
        "days_met_goal": days_met_goal,
        "total_days": (end_date - start_date).days + 1,
        "completion_rate": round((days_met_goal / ((last_logged_date - start_date).days + 1)) * 100, 1) if last_logged_date >= start_date else 0
    }
    
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
    Recalculates streaks for all active challenges.
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
    
    steps_changed = False
    
    if daily_steps:
        if daily_steps.steps != payload.steps:  # Only if changed
            daily_steps.steps = payload.steps
            steps_changed = True
    else:
        # Create new
        daily_steps = DailySteps(
            user_id=str(current_user.id),
            day=payload.day,
            steps=payload.steps
        )
        db.add(daily_steps)
        steps_changed = True
    
    await db.commit()
    await db.refresh(daily_steps)
    
    # ========== RECALCULATE STREAKS IF STEPS CHANGED ==========
    if steps_changed:
        # Find all active challenges that include this date
        challenges_query = text("""
            SELECT DISTINCT c.id
            FROM challenges c
            JOIN challenge_participants cp ON cp.challenge_id = c.id
            WHERE cp.user_id = :user_id
            AND cp.left_at IS NULL
            AND :log_date BETWEEN c.start_date AND c.end_date
        """)
        
        result = await db.execute(
            challenges_query,
            {"user_id": current_user.id, "log_date": log_date}
        )
        active_challenges = result.scalars().all()
        
        # Update streak for each affected challenge
        for challenge_id in active_challenges:
            await calculate_challenge_streak(
                user_id=str(current_user.id),
                challenge_id=str(challenge_id),
                db=db
            )
    # =========================================================
    
    return {
        "log_id": f"{daily_steps.user_id}_{daily_steps.day}",
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