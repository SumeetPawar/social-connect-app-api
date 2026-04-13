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
            cp.challenge_current_streak              AS step_streak,
            cp.previous_rank                         AS stored_previous_rank
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

    # Live rank — ROW_NUMBER over all participants, then filter for this user.
    # previous_rank uses the nightly snapshot stored in challenge_participants
    # so it doesn't shift when other users log steps during the day.
    rank        = None
    rank_change = None
    total_participants = None
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
            today_ranked AS (
                SELECT user_id, ROW_NUMBER() OVER (ORDER BY total_steps DESC, user_id ASC) AS rnk
                FROM today_totals
            )
            SELECT
                (SELECT rnk FROM today_ranked WHERE user_id = :uid) AS live_rank,
                (SELECT COUNT(*) FROM today_totals)                  AS total_participants
        """), {
            "cid":     step_challenge_id,
            "start":   cr["start_date"],
            "end_cap": cr["end_cap"],
            "uid":     uid,
        })
        lr = live_row.mappings().first()
        if lr and lr["live_rank"]:
            rank = int(lr["live_rank"])
            total_participants = int(lr["total_participants"] or 0)
            stored_prev = cr["stored_previous_rank"]
            if stored_prev:
                rank_change = int(stored_prev) - rank  # positive = moved up

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
    habit_rank             = None
    habit_rank_change      = None
    habit_total_participants = None

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

        # ── Habit ranking among pack members ─────────────────────────────
        try:
            hrank_row = await db.execute(text("""
                WITH my_challenge AS (
                    SELECT hc.id, hc.pack_id, COUNT(DISTINCT hcm.id) AS total_habits
                    FROM habit_challenges hc
                    JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
                    WHERE hc.id = :cid
                    GROUP BY hc.id
                ),
                pack_challenges AS (
                    SELECT hc.user_id, hc.id AS challenge_id,
                           COUNT(DISTINCT hcm.id) AS total_habits
                    FROM habit_challenges hc
                    JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
                    JOIN my_challenge mc ON hc.pack_id = mc.pack_id
                    WHERE hc.status = 'active'
                    GROUP BY hc.user_id, hc.id
                ),
                daily_counts AS (
                    SELECT hcm.challenge_id, dl.logged_date,
                           COUNT(*) FILTER (WHERE dl.completed) AS done
                    FROM daily_logs dl
                    JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                    GROUP BY hcm.challenge_id, dl.logged_date
                ),
                user_scores AS (
                    SELECT pc.user_id,
                           COUNT(DISTINCT CASE
                               WHEN dc.done >= GREATEST(1, CEIL(pc.total_habits::numeric / 2))
                               THEN dc.logged_date END) AS good_days
                    FROM pack_challenges pc
                    LEFT JOIN daily_counts dc ON dc.challenge_id = pc.challenge_id
                    GROUP BY pc.user_id
                ),
                -- yesterday snapshot: exclude today from good_days
                daily_counts_yest AS (
                    SELECT hcm.challenge_id, dl.logged_date,
                           COUNT(*) FILTER (WHERE dl.completed) AS done
                    FROM daily_logs dl
                    JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                    WHERE dl.logged_date < :today
                    GROUP BY hcm.challenge_id, dl.logged_date
                ),
                user_scores_yest AS (
                    SELECT pc.user_id,
                           COUNT(DISTINCT CASE
                               WHEN dc.done >= GREATEST(1, CEIL(pc.total_habits::numeric / 2))
                               THEN dc.logged_date END) AS good_days
                    FROM pack_challenges pc
                    LEFT JOIN daily_counts_yest dc ON dc.challenge_id = pc.challenge_id
                    GROUP BY pc.user_id
                ),
                ranked      AS (SELECT user_id, ROW_NUMBER() OVER (ORDER BY good_days DESC, user_id ASC) AS rnk FROM user_scores),
                ranked_yest AS (SELECT user_id, ROW_NUMBER() OVER (ORDER BY good_days DESC, user_id ASC) AS rnk FROM user_scores_yest)
                SELECT
                    (SELECT rnk FROM ranked      WHERE user_id = :uid) AS habit_rank,
                    (SELECT rnk FROM ranked_yest WHERE user_id = :uid) AS habit_rank_yesterday,
                    (SELECT COUNT(*) FROM user_scores)                  AS total_participants,
                    (SELECT pack_id FROM my_challenge)                  AS pack_id
            """), {"cid": challenge_id, "uid": uid, "today": today})
            hr2 = hrank_row.mappings().first()
            if hr2 and hr2["pack_id"] and hr2["habit_rank"]:
                habit_rank              = int(hr2["habit_rank"])
                habit_total_participants = int(hr2["total_participants"] or 0)
                if hr2["habit_rank_yesterday"]:
                    habit_rank_change = int(hr2["habit_rank_yesterday"]) - habit_rank
        except Exception:
            pass

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
        "step_challenge": {
            "id":                 step_challenge_id,
            "rank":               rank,
            "previous_rank":      int(cr["stored_previous_rank"]) if cr and cr["stored_previous_rank"] else None,
            "rank_change":        rank_change,
            "total_participants": total_participants,
        },
        "habit_challenge": {
            "rank":               habit_rank,
            "rank_change":        habit_rank_change,
            "total_participants": habit_total_participants,
        },
        "habits":       habits,
        "habit_streak": habit_streak,
        "ai_insight":   insight,
        "user": {
            "name":            user.name,
            "profile_pic_url": user.profile_pic_url,
        },
    }
