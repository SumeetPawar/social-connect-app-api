from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models.user import User
from app.models.step_log import StepLog
from app.models.daily_total import DailyTotal
from app.models.goal import Goal

from app.schemas.steps import (
    StepsAddRequest,
    StepsAddResponse,
    DayTotalResponse,
    WeekProgressResponse,
    WeekProgressDay,
)
from app.services.date_windows import week_window_monday


router = APIRouter(prefix="/steps", tags=["steps"])


def user_today(user: User):
    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    return datetime.now(tz).date()


@router.post("/add", response_model=StepsAddResponse)
async def add_steps(
    payload: StepsAddRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = payload.log_date or user_today(user)

    # 1) Insert raw log
    log = StepLog(
        user_id=user.id,
        log_date=day,
        steps=payload.steps,
        source=payload.source or "manual",
        note=payload.note,
    )
    db.add(log)

    # 2) Upsert daily_totals (atomic add)
    stmt = insert(DailyTotal).values(
        user_id=user.id,
        day=day,
        total_steps=payload.steps,   # absolute total
    ).on_conflict_do_update(
        index_elements=[DailyTotal.user_id, DailyTotal.day],
        set_={"total_steps": payload.steps}  # overwrite with latest
    ).returning(DailyTotal.total_steps)

    res = await db.execute(stmt)
    new_total = res.scalar_one()

    await db.commit()
    await db.refresh(log)

    return {
        "log_id": log.id,
        "day": day,
        "added_steps": payload.steps,
        "day_total": int(new_total),
    }


@router.get("/today", response_model=DayTotalResponse)
async def get_today_total(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = user_today(user)

    stmt = select(DailyTotal.total_steps).where(
        DailyTotal.user_id == user.id,
        DailyTotal.day == day
    )
    res = await db.execute(stmt)
    total = res.scalar_one_or_none() or 0

    return {"day": day, "total_steps": int(total)}


@router.get("/week", response_model=WeekProgressResponse)
async def get_week_progress(
    metric_key: str = "steps",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # week window from user's local time
    today = user_today(user)
    anchor_start, anchor_end = week_window_monday(today)  # anchor_end is Sunday

    # 1) Fetch current week's goal
    g_stmt = select(Goal).where(
        Goal.user_id == user.id,
        Goal.metric_key == metric_key,
        Goal.period == "week",
        Goal.anchor_start == anchor_start,
    )
    g_res = await db.execute(g_stmt)
    goal = g_res.scalar_one_or_none()

    if not goal:
        raise HTTPException(status_code=404, detail="No goal set for this week.")

    # 2) Fetch day totals for Mon..Sun
    t_stmt = select(DailyTotal.day, DailyTotal.total_steps).where(
        DailyTotal.user_id == user.id,
        DailyTotal.day >= anchor_start,
        DailyTotal.day <= anchor_end,
    ).order_by(DailyTotal.day.asc())

    t_res = await db.execute(t_stmt)
    rows = t_res.all()

    by_day = {d: int(s) for (d, s) in rows}

    # 3) Fill missing days with 0 (for UI)
    days = []
    week_total = 0
    cur = anchor_start
    while cur <= anchor_end:
        v = by_day.get(cur, 0)
        week_total += v
        days.append(WeekProgressDay(day=cur, total_steps=v))
        cur = cur.fromordinal(cur.toordinal() + 1)

    goal_period = float(goal.period_target)
    progress_pct = (week_total / goal_period * 100.0) if goal_period > 0 else 0.0
    remaining = max(0.0, goal_period - week_total)

    return {
        "anchor_start": goal.anchor_start,
        "period_start": goal.period_start,
        "period_end": goal.period_end,
        "goal_daily_target": float(goal.daily_target),
        "goal_period_target": goal_period,
        "week_total_steps": int(week_total),
        "progress_pct": float(progress_pct),
        "remaining_steps": float(remaining),
        "days": days,
    }
