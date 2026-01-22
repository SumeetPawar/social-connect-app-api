from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.goals import GoalSetRequest, GoalSetResponse
from app.services.date_windows import week_window_monday, remaining_days_inclusive
from app.models.goal import Goal
from app.models.user import User
from app.auth.deps import get_current_user

router = APIRouter(prefix="/goals", tags=["goals"])

@router.post("/set", response_model=GoalSetResponse)
async def set_goal(
    payload: GoalSetRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    today_local = datetime.now(tz).date()

    if payload.period != "week":
        raise HTTPException(status_code=400, detail="Only 'week' supported in v1.")

    anchor_start, period_end = week_window_monday(today_local)
    period_start = today_local  # fairness

    # lock check
    stmt = select(Goal).where(
        Goal.user_id == user.id,
        Goal.metric_key == payload.metric_key,
        Goal.period == payload.period,
        Goal.anchor_start == anchor_start,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Goal is locked for this week. You cannot change it mid-week.",
        )

    days = remaining_days_inclusive(period_start, period_end)
    daily_target = Decimal(str(payload.daily_target))
    period_target = daily_target * Decimal(days)

    goal = Goal(
        user_id=user.id,
        metric_key=payload.metric_key,
        period=payload.period,
        daily_target=daily_target,
        period_target=period_target,
        period_start=period_start,
        period_end=period_end,
        anchor_start=anchor_start,
    )

    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.get("/current", response_model=GoalSetResponse | None)
async def get_current_goal(
    metric_key: str = "steps",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    today_local = datetime.now(tz).date()

    anchor_start, _ = week_window_monday(today_local)

    stmt = select(Goal).where(
        Goal.user_id == user.id,
        Goal.metric_key == metric_key,
        Goal.period == "week",
        Goal.anchor_start == anchor_start,
    )
    res = await db.execute(stmt)
    g = res.scalar_one_or_none()

    if not g:
        raise HTTPException(status_code=404, detail="No goal set for this week.")
    return g
