from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models.user import User
from app.models.daily_total import DailyTotal
from app.models.goal import Goal
from app.schemas.streaks import StreakResponse, StreakDay
from app.services.date_windows import week_window_monday

router = APIRouter(prefix="/streaks", tags=["streaks"])

def user_today(user: User):
    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    return datetime.now(tz).date()

@router.get("/current", response_model=StreakResponse)
async def get_current_streak(
    metric_key: str = "steps",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = user_today(user)
    start_day = today - timedelta(days=13)

    # Fetch last 14 days totals
    t_stmt = select(DailyTotal.day, DailyTotal.total_steps).where(
        DailyTotal.user_id == user.id,
        DailyTotal.day >= start_day,
        DailyTotal.day <= today,
    )
    t_res = await db.execute(t_stmt)
    rows = t_res.all()
    by_day = {d: int(s) for (d, s) in rows}

    # Try fetch goal for current week (optional)
    daily_target = None
    try:
        anchor_start, _ = week_window_monday(today)
        g_stmt = select(Goal.daily_target).where(
            Goal.user_id == user.id,
            Goal.metric_key == metric_key,
            Goal.period == "week",
            Goal.anchor_start == anchor_start,
        )
        g_res = await db.execute(g_stmt)
        dt = g_res.scalar_one_or_none()
        daily_target = float(dt) if dt is not None else None
    except Exception:
        daily_target = None

    last_14 = []
    cursor = start_day
    while cursor <= today:
        total = by_day.get(cursor, 0)
        habit_done = total > 0
        goal_done = (daily_target is not None) and (total >= daily_target)

        last_14.append(StreakDay(
            day=cursor,
            total_steps=total,
            habit_done=habit_done,
            goal_done=goal_done,
        ))
        cursor = cursor + timedelta(days=1)

    # Habit streak (logging streak)
    habit_streak = 0
    for d in reversed(last_14):
        if d.habit_done:
            habit_streak += 1
        else:
            break

    # Goal streak (only meaningful if goal exists)
    goal_streak = 0
    if daily_target is not None:
        for d in reversed(last_14):
            if d.goal_done:
                goal_streak += 1
            else:
                break

    today_total = by_day.get(today, 0)
    habit_today_done = today_total > 0
    goal_today_done = (daily_target is not None) and (today_total >= daily_target)

    return StreakResponse(
        metric_key=metric_key,
        today=today,
        today_total=today_total,
        habit_today_done=habit_today_done,
        goal_today_done=goal_today_done,
        habit_streak=habit_streak,
        goal_streak=goal_streak,
        last_14_days=last_14,
    )
