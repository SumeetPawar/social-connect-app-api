"""
GET /api/home  —  single endpoint that feeds the home screen.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.deps import get_db
from app.models import User
from app.services.ai_insight import get_home_insight
from app.services.habits_service import get_streak

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("")
async def home(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today     = date.today()
    yesterday = today - timedelta(days=1)
    uid       = str(user.id)

    # ── Steps ────────────────────────────────────────────────────────────────
    steps_row = await db.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN day = :yesterday THEN steps ELSE 0 END), 0) AS steps_yesterday,
            COALESCE(SUM(CASE WHEN day = :today     THEN steps ELSE 0 END), 0) AS steps_today
        FROM daily_steps
        WHERE user_id = :uid
    """), {"uid": uid, "yesterday": yesterday, "today": today})
    sr = steps_row.mappings().first() or {}
    steps_yesterday = int(sr.get("steps_yesterday") or 0)
    steps_today     = int(sr.get("steps_today")     or 0)

    # ── Active step challenge ─────────────────────────────────────────────────
    challenge_row = await db.execute(text("""
        SELECT
            c.id                                     AS challenge_id,
            c.start_date,
            LEAST(c.end_date, :today)                AS end_cap,
            COALESCE(cp.selected_daily_target, 8000) AS daily_target,
            cp.challenge_current_streak              AS step_streak
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = :uid
          AND c.status   = 'active'
          AND c.end_date >= :today
          AND cp.left_at IS NULL
        ORDER BY c.end_date DESC
        LIMIT 1
    """), {"uid": uid, "today": today})
    cr = challenge_row.mappings().first()

    daily_target      = int(cr["daily_target"])   if cr else 8000
    step_challenge_id = str(cr["challenge_id"])   if cr else None
    step_streak       = int(cr["step_streak"])     if cr and cr["step_streak"] else 0
    steps_pct         = round(steps_today / daily_target * 100) if daily_target else 0

    # Live rank + yesterday rank — both computed live, no stored snapshot needed
    rank        = None
    rank_change = None
    if cr:
        live_row = await db.execute(text("""
            WITH today_totals AS (
                SELECT cp.user_id,
                       COALESCE(SUM(ds.steps), 0) AS total_steps
                FROM challenge_participants cp
                LEFT JOIN daily_steps ds
                    ON ds.user_id = cp.user_id
                    AND ds.day >= :start
                    AND ds.day <= :end_cap
                WHERE cp.challenge_id = :cid AND cp.left_at IS NULL
                GROUP BY cp.user_id
            ),
            yesterday_totals AS (
                SELECT cp.user_id,
                       COALESCE(SUM(ds.steps), 0) AS total_steps
                FROM challenge_participants cp
                LEFT JOIN daily_steps ds
                    ON ds.user_id = cp.user_id
                    AND ds.day >= :start
                    AND ds.day <= :yesterday
                WHERE cp.challenge_id = :cid AND cp.left_at IS NULL
                GROUP BY cp.user_id
            )
            SELECT
                (SELECT ROW_NUMBER() OVER (ORDER BY total_steps DESC)
                 FROM today_totals WHERE user_id = :uid) AS live_rank,
                (SELECT ROW_NUMBER() OVER (ORDER BY total_steps DESC)
                 FROM yesterday_totals WHERE user_id = :uid) AS yesterday_rank
        """), {
            "cid":       step_challenge_id,
            "start":     cr["start_date"],
            "end_cap":   cr["end_cap"],
            "yesterday": yesterday,
            "uid":       uid,
        })
        lr = live_row.mappings().first()
        if lr and lr["live_rank"]:
            rank = int(lr["live_rank"])
            if lr["yesterday_rank"]:
                rank_change = int(lr["yesterday_rank"]) - rank  # positive = moved up

    # ── Habits ───────────────────────────────────────────────────────────────
    habit_row = await db.execute(text("""
        SELECT
            hc.id                    AS challenge_id,
            hc.started_at,
            hc.ends_at,
            COUNT(DISTINCT hcm.id)   AS total_habits,
            COALESCE(SUM(CASE WHEN dl.logged_date = :today     AND dl.completed THEN 1 ELSE 0 END), 0) AS done_today,
            COALESCE(SUM(CASE WHEN dl.logged_date = :yesterday AND dl.completed THEN 1 ELSE 0 END), 0) AS done_yesterday
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hc.id
        LIMIT 1
    """), {"uid": uid, "today": today, "yesterday": yesterday})
    hr = habit_row.mappings().first()

    habits     = None
    habit_streak = {"current": 0, "longest": 0, "perfect_days": 0}

    if hr:
        total_habits    = int(hr["total_habits"])
        done_today      = int(hr["done_today"])
        done_yesterday  = int(hr["done_yesterday"])
        challenge_id    = int(hr["challenge_id"])
        day_number      = (today - hr["started_at"]).days + 1 if hr["started_at"] else 1
        total_days      = (hr["ends_at"] - hr["started_at"]).days + 1 if hr["started_at"] else 21

        habits = {
            "challenge_id":          challenge_id,
            "day_number":            day_number,
            "total_days":            total_days,
            # Today
            "completed_count":       done_today,
            "total_count":           total_habits,
            "all_done":              done_today >= total_habits,
            # Yesterday
            "yesterday_completed":   done_yesterday,
            "yesterday_all_done":    done_yesterday >= total_habits,
        }

        # Proper habit streak from habits_service (consecutive perfect days)
        try:
            streak_data = await get_streak(db, challenge_id, uid)
            habit_streak = {
                "current":           streak_data.get("current_streak", 0),
                "effective":         streak_data.get("effective_streak", 0),
                "longest":           streak_data.get("longest_streak", 0),
                "perfect_days":      streak_data.get("perfect_days", 0),
            }
        except Exception:
            pass  # returns zero-dict above

    # ── AI insight ────────────────────────────────────────────────────────────
    insight = await get_home_insight(db, uid)

    return {
        "steps": {
            "yesterday":    steps_yesterday,
            "today":        steps_today,
            "daily_target": daily_target,
            "pct":          steps_pct,
            "step_streak":  step_streak,
        },
        "challenge": {
            "id":          step_challenge_id,
            "rank":        rank,
            "rank_change": rank_change,
        },
        "habits":       habits,
        "habit_streak": habit_streak,
        "ai_insight":   insight,
        "user": {
            "name":            user.name,
            "profile_pic_url": user.profile_pic_url,
        },
    }
