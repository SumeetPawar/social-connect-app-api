from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.services.reminder_service import (
    send_step_reminders,
    send_streak_at_risk,
    send_challenge_step_nudges,
    send_test_notification_to_user,
    create_next_monthly_challenge_and_enroll_users,
    send_habit_morning_reminder,
    send_habit_evening_nudge,
    send_weekly_summary,
    send_rank_change_notifications,
    send_habit_cycle_summary,
)
from app.services.ai_insight import generate_nightly_insights
import logging


logger = logging.getLogger(__name__)

# Create scheduler instance - will be started from main.py
# misfire_grace_time=1: jobs missed by more than 1 second are skipped on restart
# (avoids a flood of WARNING logs for jobs that fired while the app was offline)
scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 3600, "coalesce": True})


async def update_all_previous_ranks():
    """
    Snapshot today's leaderboard ranks into previous_rank and previous_consistency_rank
    for all participants in all active challenges.

    This runs once per day at midnight. The API calculates the live current rank,
    and the frontend compares it against previous_rank to show rank shifts.

    IMPORTANT: Uses challenge start_date as the step filter start, matching the API exactly.
    """
    logger.info("Starting daily previous_rank snapshot job")
    async with AsyncSessionLocal() as db:
        try:
            from sqlalchemy import text
            from datetime import date
            today = date.today()

            challenges = await db.execute(text("SELECT id, start_date, end_date FROM challenges WHERE status = 'active'"))
            challenge_rows = challenges.mappings().all()
            logger.info(f"Found {len(challenge_rows)} active challenges to process")

            for challenge in challenge_rows:
                challenge_id = challenge['id']
                start_date = challenge['start_date']
                end_date = challenge['end_date']
                challenge_end_or_today = min(end_date, today)
                total_days = (challenge_end_or_today - start_date).days + 1

                logger.info(f"Processing challenge {challenge_id} | start={start_date} | end={end_date} | total_days={total_days}")

                # ── Step leaderboard (matches API exactly) ──────────────────────────────
                # API uses: ds.day >= :start_date AND ds.day <= :end_date_or_today
                leaderboard = await db.execute(text('''
                    WITH user_totals AS (
                        SELECT
                            cp.user_id,
                            COALESCE(SUM(ds.steps), 0) AS total_steps
                        FROM challenge_participants cp
                        LEFT JOIN daily_steps ds
                            ON ds.user_id = cp.user_id
                            AND ds.day >= :start_date
                            AND ds.day <= :end_date_or_today
                        WHERE cp.challenge_id = :challenge_id
                          AND cp.left_at IS NULL
                        GROUP BY cp.user_id
                    ),
                    ranked AS (
                        SELECT
                            user_id,
                            ROW_NUMBER() OVER (ORDER BY total_steps DESC, user_id ASC) AS rank
                        FROM user_totals
                    )
                    SELECT user_id, rank FROM ranked
                '''), {
                    "challenge_id": challenge_id,
                    "start_date": start_date,
                    "end_date_or_today": challenge_end_or_today
                })
                leaderboard_rows = leaderboard.mappings().all()
                logger.info(
                    f"[{challenge_id}] Step ranks: " +
                    ", ".join([f"{u['user_id']}→{u['rank']}" for u in leaderboard_rows])
                )
                for user in leaderboard_rows:
                    await db.execute(
                        text("""
                            UPDATE challenge_participants
                            SET previous_rank = :rank
                            WHERE challenge_id = :challenge_id AND user_id = :user_id
                        """),
                        {"rank": user["rank"], "challenge_id": challenge_id, "user_id": user["user_id"]}
                    )

                # ── Consistency leaderboard (matches API exactly) ─────────────────────
                # API sorts by: completion_pct DESC, total_steps DESC  (Python enumerate = ROW_NUMBER)
                consistency = await db.execute(text('''
                    WITH user_totals AS (
                        SELECT
                            cp.user_id,
                            COALESCE(SUM(ds.steps), 0) AS total_steps,
                            COUNT(DISTINCT ds.day) FILTER (
                                WHERE ds.steps >= cp.selected_daily_target
                            ) AS days_met_goal
                        FROM challenge_participants cp
                        LEFT JOIN daily_steps ds
                            ON ds.user_id = cp.user_id
                            AND ds.day >= :start_date
                            AND ds.day <= :end_date_or_today
                        WHERE cp.challenge_id = :challenge_id
                          AND cp.left_at IS NULL
                        GROUP BY cp.user_id, cp.selected_daily_target
                    ),
                    ranked AS (
                        SELECT
                            user_id,
                            CASE WHEN :total_days > 0
                                 THEN ROUND((days_met_goal::numeric / :total_days) * 100, 1)
                                 ELSE 0
                            END AS completion_pct,
                            total_steps,
                            ROW_NUMBER() OVER (
                                ORDER BY
                                    CASE WHEN :total_days > 0
                                         THEN ROUND((days_met_goal::numeric / :total_days) * 100, 1)
                                         ELSE 0
                                    END DESC,
                                    total_steps DESC,
                                    user_id ASC
                            ) AS consistency_rank
                        FROM user_totals
                    )
                    SELECT user_id, consistency_rank FROM ranked
                '''), {
                    "challenge_id": challenge_id,
                    "start_date": start_date,
                    "end_date_or_today": challenge_end_or_today,
                    "total_days": total_days
                })
                consistency_rows = consistency.mappings().all()
                logger.info(
                    f"[{challenge_id}] Consistency ranks: " +
                    ", ".join([f"{u['user_id']}→{u['consistency_rank']}" for u in consistency_rows])
                )
                for user in consistency_rows:
                    await db.execute(
                        text("""
                            UPDATE challenge_participants
                            SET previous_consistency_rank = :consistency_rank
                            WHERE challenge_id = :challenge_id AND user_id = :user_id
                        """),
                        {"consistency_rank": user["consistency_rank"], "challenge_id": challenge_id, "user_id": user["user_id"]}
                    )

            await db.commit()
            logger.info("Completed daily previous_rank snapshot job")
        except Exception as e:
            logger.error(f"Error in previous_rank snapshot job: {e}", exc_info=True)


async def check_and_send_reminders():
    """Wrapper to get DB session and send reminders"""
    logger.info("Starting hourly step reminder check")
    async with AsyncSessionLocal() as db:
        try:
            await send_step_reminders(db)
            logger.info("Completed hourly step reminder check")
        except Exception as e:
            logger.error(f"Error in reminder job: {e}", exc_info=True)


async def check_streak_at_risk():
    async with AsyncSessionLocal() as db:
        try:
            await send_streak_at_risk(db)
        except Exception as e:
            logger.error(f"Error in streak-at-risk job: {e}", exc_info=True)


async def nudge_challenge_participants():
    async with AsyncSessionLocal() as db:
        try:
            await send_challenge_step_nudges(db)
        except Exception as e:
            logger.error(f"Error in challenge step nudge job: {e}", exc_info=True)


# ─── Configure all jobs ───────────────────────────────────────────────────────

# 1. Daily rank snapshot — 00:05 (still needed for leaderboard UI)
scheduler.add_job(
    update_all_previous_ranks,
    CronTrigger(hour=0, minute=5, timezone="Asia/Kolkata"),
    id='update_previous_ranks',
    replace_existing=True,
)
logger.info("Job configured: rank snapshot @ 00:05 IST daily")

# 2. Streak-at-risk alert — 20:00 (8 PM) IST
scheduler.add_job(
    check_streak_at_risk,
    CronTrigger(hour=20, minute=0, timezone="Asia/Kolkata"),
    id='streak_at_risk',
    replace_existing=True,
)
logger.info("Job configured: streak-at-risk alert @ 20:00 IST daily")

# 3. Evening step reminder — 21:00 (9 PM) IST
scheduler.add_job(
    check_and_send_reminders,
    CronTrigger(hour=21, minute=0, timezone="Asia/Kolkata"),
    id='step_reminders',
    replace_existing=True,
)
logger.info("Job configured: step reminder @ 21:00 IST daily")

# 4. Challenge step nudges — 12:00 (noon) and 16:00 (4 PM)
scheduler.add_job(
    nudge_challenge_participants,
    CronTrigger(hour=12, minute=0, timezone="Asia/Kolkata"),
    id='challenge_nudge_noon',
    replace_existing=True,
)
logger.info("Job configured: challenge nudges @ 12:00 IST daily")

scheduler.add_job(
    nudge_challenge_participants,
    CronTrigger(hour=16, minute=0, timezone="Asia/Kolkata"),
    id='challenge_nudge_4pm',
    replace_existing=True,
)
logger.info("Job configured: challenge nudges @ 16:00 IST daily")


# ─── Habit challenge jobs ──────────────────────────────────────────────────────

async def habit_morning_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_habit_morning_reminder(db)
        except Exception as e:
            logger.error(f"Error in habit morning reminder job: {e}", exc_info=True)


async def habit_evening_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_habit_evening_nudge(db)
        except Exception as e:
            logger.error(f"Error in habit evening nudge job: {e}", exc_info=True)


async def weekly_summary_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_weekly_summary(db)
        except Exception as e:
            logger.error(f"Error in weekly summary job: {e}", exc_info=True)


async def rank_change_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_rank_change_notifications(db)
        except Exception as e:
            logger.error(f"Error in rank change notification job: {e}", exc_info=True)


# 5. Habit morning reminder — 07:30 IST
scheduler.add_job(
    habit_morning_job,
    CronTrigger(hour=7, minute=30, timezone="Asia/Kolkata"),
    id='habit_morning_reminder',
    replace_existing=True,
)
logger.info("Job configured: habit morning reminder @ 07:30 IST daily")

# 6. Habit evening nudge — 20:30 IST
scheduler.add_job(
    habit_evening_job,
    CronTrigger(hour=20, minute=30, timezone="Asia/Kolkata"),
    id='habit_evening_nudge',
    replace_existing=True,
)
logger.info("Job configured: habit evening nudge @ 20:30 IST daily")

# 7. Weekly summary — Sunday 20:00 IST
scheduler.add_job(
    weekly_summary_job,
    CronTrigger(day_of_week='sun', hour=20, minute=0, timezone="Asia/Kolkata"),
    id='weekly_summary',
    replace_existing=True,
)
logger.info("Job configured: weekly summary @ 20:00 IST every Sunday")

# 8. Nightly AI insight generation — 00:30 IST (after midnight, uses yesterday's data)
async def nightly_insight_job():
    async with AsyncSessionLocal() as db:
        try:
            await generate_nightly_insights(db)
        except Exception as e:
            logger.error(f"Error in nightly insight job: {e}", exc_info=True)


scheduler.add_job(
    nightly_insight_job,
    CronTrigger(hour=7, minute=15, timezone="Asia/Kolkata"),
    id='nightly_ai_insights',
    replace_existing=True,
)
logger.info("Job configured: nightly AI insight generation @ 03:06  IST daily")

# 9. Habit cycle completion summary — 21:30 IST (challenges ending today)
async def habit_cycle_summary_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_habit_cycle_summary(db)
        except Exception as e:
            logger.error(f"Error in habit cycle summary job: {e}", exc_info=True)


scheduler.add_job(
    habit_cycle_summary_job,
    CronTrigger(hour=21, minute=33, timezone="Asia/Kolkata"),
    id='habit_cycle_summary',
    replace_existing=True,
)
logger.info("Job configured: habit cycle summary @ 21:33 IST daily")

# 9. Rank change notifications — 21:30 IST (after step reminders, using day's snapshot)
# scheduler.add_job(
#     rank_change_job,
#     CronTrigger(hour=21, minute=30, timezone="Asia/Kolkata"),
#     id='rank_change_notifications',
#     replace_existing=True,
# )
# logger.info("Job configured: rank change notifications @ 21:30 IST daily")

# ─── TEST JOB: send one sample message every 10 mins to a specific user ───────
async def _test_notification_job():
    async with AsyncSessionLocal() as db:
        try:
            await send_test_notification_to_user(db)
        except Exception as e:
            logger.error(f"Error in test notification job: {e}", exc_info=True)


# ─── Monthly Challenge Auto-Creation Job ──────────────────────────────────────
async def monthly_challenge_job():
    async with AsyncSessionLocal() as db:
        try:
            await create_next_monthly_challenge_and_enroll_users(db)
        except Exception as e:
            logger.error(f"Error in monthly challenge creation job: {e}", exc_info=True)

# Run at 23:55 on the last day of each month
from apscheduler.triggers.cron import CronTrigger
scheduler.add_job(
    monthly_challenge_job,
    CronTrigger(hour=23, minute=55, day='last'),
    id='monthly_challenge_creation',
    replace_existing=True,
)
logger.info("Job configured: monthly challenge creation @ 23:55 last day of month")

# scheduler.add_job(
#     _test_notification_job,
#     "interval",
#     minutes=4,
#     id="test_notification",
#     replace_existing=True,
# )
# logger.info("TEST JOB configured: sample notification every 4 mins")
