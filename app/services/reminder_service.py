from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from app.models import User, DailySteps, PushSubscription
from app.services.push_notify import send_web_push, PushResult
import logging

logger = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _get_subscriptions(db: AsyncSession, user_id: str) -> list:
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == str(user_id))
    )
    return result.scalars().all()


async def _push_all(db: AsyncSession, subscriptions, message: dict) -> int:
    """Fire notification to every device. Auto-deletes expired subscriptions."""
    sent = 0
    for sub in subscriptions:
        result = send_web_push(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            message,
        )
        if result == PushResult.OK:
            sent += 1
        elif result == PushResult.EXPIRED:
            logger.info(f"Deleting expired subscription {sub.id}")
            await db.delete(sub)
            await db.commit()
        # PushResult.ERROR: log already done inside send_web_push, just skip
    return sent


async def _users_with_subscriptions(db: AsyncSession):
    result = await db.execute(
        select(User)
        .join(PushSubscription, User.id == PushSubscription.user_id)
        .distinct()
    )
    return result.scalars().all()


async def _steps_today(db: AsyncSession, user_id: str, today: date):
    result = await db.execute(
        select(DailySteps).where(
            DailySteps.user_id == str(user_id),
            DailySteps.day == today,
        )
    )
    row = result.scalar_one_or_none()
    return row.steps if row else 0


# ─── 1. Evening step reminder (already existed, preserved) ────────────────────

async def send_step_reminders(db: AsyncSession):
    """
    9 PM daily — send reminder only to users who haven't logged any steps today.
    Respects each user's timezone.
    """
    logger.info("JOB: evening step reminder")
    users = await _users_with_subscriptions(db)
    notified = 0
    for user in users:
        try:
            tz = ZoneInfo(user.timezone or "Asia/Kolkata")
            now = datetime.now(tz)
            if now.hour < 9:
                continue
            steps = await _steps_today(db, user.id, now.date())
            if steps == 0:
                subs = await _get_subscriptions(db, user.id)
                sent = await _push_all(db, subs, {
                    "title": "⏰ Step Reminder",
                    "body": "Don't forget to log your steps today! Keep your streak going 🔥",
                    "url": "/steps",
                })
                if sent:
                    notified += 1
        except Exception as e:
            logger.error(f"Step reminder error for user {user.id}: {e}")
    logger.info(f"Evening step reminder: notified {notified} users")
    return notified


# ─── 2. Streak-at-risk alert (8 PM) ──────────────────────────────────────────

async def send_streak_at_risk(db: AsyncSession):
    """
    8 PM daily — warn users with an active streak (> 0) who haven't logged today.
    Gives them one hour heads-up before the reminder.
    """
    logger.info("JOB: streak-at-risk check")
    users = await _users_with_subscriptions(db)
    notified = 0
    for user in users:
        try:
            streak = getattr(user, "global_current_streak", 0) or 0
            if streak == 0:
                continue  # no streak to protect
            tz = ZoneInfo(user.timezone or "Asia/Kolkata")
            now = datetime.now(tz)
            steps = await _steps_today(db, user.id, now.date())
            if steps == 0:
                subs = await _get_subscriptions(db, user.id)
                sent = await _push_all(db, subs, {
                    "title": f"🔥 Streak at risk — {streak} day{'s' if streak != 1 else ''}!",
                    "body": "Log your steps now to keep your streak alive!",
                    "url": "/steps",
                })
                if sent:
                    notified += 1
        except Exception as e:
            logger.error(f"Streak-at-risk error for user {user.id}: {e}")
    logger.info(f"Streak-at-risk: notified {notified} users")
    return notified


# ─── 3. Rank change notification (runs after midnight rank snapshot) ──────────

async def send_rank_change_notifications(db: AsyncSession):
    """
    Runs after the 00:05 rank snapshot.
    Notifies challenge participants whose rank improved or dropped.
    Reads previous_rank vs live current rank from challenge_participants.
    """
    logger.info("JOB: rank change notifications")
    today = date.today()

    rows = await db.execute(text("""
        SELECT
            cp.user_id,
            cp.challenge_id,
            c.title AS challenge_title,
            cp.previous_rank,
            ROW_NUMBER() OVER (
                PARTITION BY cp.challenge_id
                ORDER BY COALESCE(SUM(ds.steps), 0) DESC
            ) AS current_rank
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        LEFT JOIN daily_steps ds
            ON ds.user_id = cp.user_id
            AND ds.day >= c.start_date
            AND ds.day <= LEAST(c.end_date, :today)
        WHERE c.status = 'active'
          AND cp.left_at IS NULL
          AND cp.previous_rank IS NOT NULL
        GROUP BY cp.user_id, cp.challenge_id, c.title, cp.previous_rank, c.start_date, c.end_date
    """), {"today": today})

    notified = 0
    for row in rows.mappings():
        prev = row["previous_rank"]
        curr = row["current_rank"]
        if prev is None or prev == curr:
            continue
        change = prev - curr  # positive = moved UP (lower number is better)
        if change > 0:
            title = f"📈 You moved up to #{curr}!"
            body = f"Great work in {row['challenge_title']}! You climbed {change} spot{'s' if change != 1 else ''}."
        else:
            title = f"📉 You dropped to #{curr}"
            body = f"Someone overtook you in {row['challenge_title']}. Log more steps to reclaim your spot!"
        try:
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {"title": title, "body": body, "url": "/challenges"})
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Rank change notify error for user {row['user_id']}: {e}")

    logger.info(f"Rank change notifications: notified {notified} users")
    return notified


# ─── 4. Weekly summary (Sunday 8 PM) ─────────────────────────────────────────

async def send_weekly_summary(db: AsyncSession):
    """
    Every Sunday at 8 PM — personal step total for the past 7 days
    + current position in each active challenge.
    """
    logger.info("JOB: weekly summary")
    today = date.today()
    week_ago = today - timedelta(days=6)

    users = await _users_with_subscriptions(db)
    notified = 0
    for user in users:
        try:
            # Steps this week
            result = await db.execute(text("""
                SELECT COALESCE(SUM(steps), 0) AS total
                FROM daily_steps
                WHERE user_id = :uid AND day >= :start AND day <= :end
            """), {"uid": str(user.id), "start": week_ago, "end": today})
            weekly_steps = result.scalar() or 0

            # Active challenge count
            result2 = await db.execute(text("""
                SELECT COUNT(*) FROM challenge_participants cp
                JOIN challenges c ON c.id = cp.challenge_id
                WHERE cp.user_id = :uid AND c.status = 'active' AND cp.left_at IS NULL
            """), {"uid": str(user.id)})
            active_challenges = result2.scalar() or 0

            body = f"{weekly_steps:,} steps this week"
            if active_challenges:
                body += f" · active in {active_challenges} challenge{'s' if active_challenges != 1 else ''}"

            subs = await _get_subscriptions(db, user.id)
            sent = await _push_all(db, subs, {
                "title": "📊 Your Weekly Summary",
                "body": body,
                "url": "/steps",
            })
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Weekly summary error for user {user.id}: {e}")

    logger.info(f"Weekly summary: notified {notified} users")
    return notified
