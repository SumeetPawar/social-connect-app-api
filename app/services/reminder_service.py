from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import random

from app.models import (
    User, DailySteps, PushSubscription,
    HabitChallenge, HabitCommitment, DailyLog, ChallengeStatus,
    DailyPushCount,
)
from app.services.push_notify import send_web_push, PushResult
import logging

logger = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

# Daily push cap — stored in DB so it survives restarts and is shared across
# worker processes. Real-time notifications (perfect day, milestone) bypass this.

async def _try_claim_push_slot(db: AsyncSession, user_id: str, limit: int = 3) -> bool:
    """
    Atomically try to claim one push slot for today.

    Uses a single PostgreSQL upsert so the check-and-increment is race-free
    across restarts and multiple worker processes.

    Returns True if a slot was claimed (count was < limit).
    Returns False if the daily cap is already reached.
    """
    result = await db.execute(text("""
        INSERT INTO daily_push_counts (user_id, push_date, count)
        VALUES (:uid, :today, 1)
        ON CONFLICT (user_id, push_date)
        DO UPDATE SET count = daily_push_counts.count + 1
        WHERE daily_push_counts.count < :limit
        RETURNING count
    """), {"uid": str(user_id), "today": date.today(), "limit": limit})
    await db.commit()
    return result.scalar_one_or_none() is not None


# ─── Monthly Challenge Auto-Creation ──────────────────────────────────────────
from sqlalchemy import insert
import calendar

async def create_next_monthly_challenge_and_enroll_users(db: AsyncSession):
    """
    On the last day of the month, create a new step challenge for next month and enroll all users.
    Each user is enrolled with their current daily goal (from their most recent active challenge or default 8000).
    """
    today = date.today()
    # Compute next month start/end
    if today.month == 12:
        next_month = 1
        year = today.year + 1
    else:
        next_month = today.month + 1
        year = today.year

    start_date = date(year, next_month, 1)
    last_day = calendar.monthrange(year, next_month)[1]
    end_date = date(year, next_month, last_day)

    # Get all departments
    departments = await db.execute(select(__import__('app.models').models.Department))
    departments = departments.scalars().all()

    total_challenges = 0
    total_enrollments = 0
    for dept in departments:
        dept_title = f"{start_date.strftime('%B')} Steps Challenge"
        dept_period = "month"
        dept_scope = "department"

        # Check for existing challenge for this department, month, and year
        existing = await db.execute(text('''
            SELECT c.id FROM challenges c
            JOIN challenge_departments cd ON cd.challenge_id = c.id
            WHERE cd.department_id = :dept_id
              AND c.start_date = :start_date
              AND c.end_date = :end_date
              AND c.title = :title
        '''), {"dept_id": str(dept.id), "start_date": start_date, "end_date": end_date, "title": dept_title})
        existing_row = existing.first()
        if existing_row:
            old_challenge_id = existing_row[0]
            # Delete participants
            await db.execute(text('DELETE FROM challenge_participants WHERE challenge_id = :cid'), {"cid": old_challenge_id})
            # Delete department link
            await db.execute(text('DELETE FROM challenge_departments WHERE challenge_id = :cid'), {"cid": old_challenge_id})
            # Delete challenge
            await db.execute(text('DELETE FROM challenges WHERE id = :cid'), {"cid": old_challenge_id})

        # Create challenge for this department
        result = await db.execute(
            insert(__import__('app.models').models.Challenge).values(
                title=dept_title,
                description=f"Monthly step challenge for {dept.name} department.",
                period=dept_period,
                scope=dept_scope,
                start_date=start_date,
                end_date=end_date,
                status="active",
            ).returning(__import__('app.models').models.Challenge.id)
        )
        challenge_id = result.scalar_one()
        # Link challenge to department
        await db.execute(
            insert(__import__('app.models').models.ChallengeDepartment).values(
                challenge_id=challenge_id,
                department_id=dept.id
            )
        )
        # Get all users in this department
        users = await db.execute(select(User).where(User.department_id == dept.id))
        users = users.scalars().all()
        for user in users:
            target_result = await db.execute(text('''
                SELECT cp.selected_daily_target AS daily_target
                FROM challenge_participants cp
                JOIN challenges c ON c.id = cp.challenge_id
                WHERE cp.user_id = :user_id AND c.status = 'active' AND cp.left_at IS NULL
                ORDER BY c.start_date DESC LIMIT 1
            '''), {"user_id": user.id})
            row = target_result.mappings().first()
            daily_target = int(row["daily_target"]) if row and row["daily_target"] else 5000
            await db.execute(
                insert(__import__('app.models').models.ChallengeParticipant).values(
                    challenge_id=challenge_id,
                    user_id=user.id,
                    selected_daily_target=daily_target,
                )
            )
            total_enrollments += 1
        total_challenges += 1
    await db.commit()
    logger.info(f"Created {total_challenges} department-wise monthly challenges and enrolled {total_enrollments} users.")

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


async def _had_steps_recently(db: AsyncSession, user_id: str, days: int = 3) -> bool:
    """True if the user logged any steps in the last N calendar days (excluding today)."""
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(DailySteps).where(
            DailySteps.user_id == str(user_id),
            DailySteps.day >= since,
            DailySteps.day < date.today(),
            DailySteps.steps > 0,
        )
    )
    return result.scalar_one_or_none() is not None


def _is_nudge_day(now: datetime) -> bool:
    """Inactive users get nudges only 4x/week — Mon, Wed, Fri, Sun."""
    return now.weekday() in (0, 2, 4, 6)  # Mon=0 Wed=2 Fri=4 Sun=6


async def _get_active_challenge_id(db: AsyncSession, user_id: str) -> str:
    """Returns the user's active challenge id where end_date is in the future, else 'main'."""
    today = date.today()
    result = await db.execute(text("""
        SELECT cp.challenge_id
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id  = :user_id
          AND c.status    = 'active'
          AND c.end_date  >= :today
          AND cp.left_at IS NULL
        LIMIT 1
    """), {"user_id": str(user_id), "today": today})
    row = result.scalar_one_or_none()
    return str(row) if row else "main"


# ─── message pools ────────────────────────────────────────────────────────────
# Tone: low pressure · quick action · progress identity · self-kindness · short & sweet
# Title ≤ 50 chars · Body ≤ 100 chars

# 9 PM — user hasn't logged any steps today
_EVENING_POOL = [
    ("Hey {name}, one quick tap 👣",
     "Log it and call today done."),
    ("{name}, no pressure — just log it 🌙",
     "Even a slow day is worth logging."),
    ("Small day? Still counts, {name} ✅",
     "Tap and close today."),
    ("{name}, you moved more than you think 🚶",
     "Around the house counts too. Log it."),
    ("Be kind to yourself, {name} 💛",
     "Log what you have. Rest easy."),
    ("Progress isn't always big, {name} 📊",
     "A logged day keeps your story going."),
    ("{name}, future you will thank today's you 🌱",
     "Even quiet days count. Log it."),
    ("One tap, {name}. That's it. 👍",
     "No judgment. Just log it."),
    ("{name}, you're a step person 👟",
     "Log today and keep that identity going."),
    ("Tired? That's okay, {name} 😌",
     "Rest days count too. Log it."),
    ("{name}, consistency > perfection 🎯",
     "No perfect day needed. Just a logged one."),
    ("Still counts, {name} 💬",
     "The walk, the stairs — it all counts."),
]

# 8 PM — user has an active streak but 0 steps today
_STREAK_RISK_POOL = [
    ("{name}, {streak} days — still yours 🔥",
     "A short walk keeps it alive."),
    ("Quick walk, {name}? {streak} days say yes 🚶",
     "10 minutes is all it takes."),
    ("{name}, your {streak}-day self shows up 💛",
     "One small move and today joins the streak."),
    ("Be kind, keep the streak, {name} 🛡️",
     "Don't let tonight be the gap."),
    ("Still time, {name} 🌙",
     "A short walk and your streak is safe."),
    ("{name}, {streak} days of showing up 🌱",
     "Move a little, log it, sleep well."),
    ("Progress, not perfection — {name} 💎",
     "Add one more tonight."),
    ("{name}, you started this streak for a reason 🎯",
     "Walk, log, rest. Streak intact."),
    ("Low energy day? Still go, {name} 😌",
     "A slow walk still counts."),
    ("{name}, the {streak}-day version of you moves 💪",
     "Log before midnight and you're good."),
]

# Noon — challenge, 0 steps, no streak
_NUDGE_COLD_POOL = [
    ("{name}, small start in {challenge} 👟",
     "Even 500 steps gets you on the board."),
    ("Just show up, {name} 🌱",
     "A short walk and you're in."),
    ("{name}, any steps count in {challenge} 💛",
     "Stairs, a walk, a lap — log it."),
    ("Be kind to yourself, {name} 😌",
     "A few steps in {challenge} still count."),
    ("{name}, one small step 🎯",
     "First log is the hardest. Then it flows."),
    ("You belong in {challenge}, {name} 💬",
     "No judgment. Just show up and log."),
]

# Noon — challenge, 0 steps, streak > 0
_NUDGE_STREAK_POOL = [
    ("{name}, {streak} days — keep it gentle 💛",
     "A small walk in {challenge} keeps the streak alive."),
    ("Low effort, big reward, {name} 🌱",
     "A short walk protects all {streak} days."),
    ("{name}, be kind — then log it 😌",
     "A slow walk in {challenge} still counts."),
    ("Identity check, {name} 🎯",
     "One step in {challenge} proves it."),
    ("{streak} days says you show up, {name} 🛡️",
     "Even a small effort keeps the story going."),
    ("{name}, progress is progress 💎",
     "Perfection not required. Just show up."),
]

# Noon — challenge, logged but below today's target
# {steps} = total challenge steps so far · {pct} = challenge completion %
_NUDGE_BELOW_POOL = [
    ("{name}, {pct}% through {challenge} 🌱",
     "{steps:,} steps in so far. Keep it going."),
    ("Good effort, {name} 💛",
     "{pct}% done in {challenge}. Every step adds up."),
    ("{name}, {pct}% and climbing 📊",
     "{steps:,} total in {challenge}. You're on track."),
    ("Looking good, {name} 😌",
     "{steps:,} steps in {challenge} so far. Stay consistent."),
    ("{name}, {pct}% — keep the pace 🎯",
     "{steps:,} in {challenge}. Small steps, big results."),
    ("You're doing it, {name} 🏅",
     "{pct}% through {challenge}. {steps:,} steps and counting."),
]


# 7:30 AM — user has active habit challenge but nothing logged yet today
_HABIT_MORNING_POOL = [
    ("{name}, your habits are waiting 🌅",
     "Start with one. The rest follow."),
    ("Morning, {name} — habit time 🌿",
     "Small steps today build big change."),
    ("{name}, fresh day, fresh habits ✨",
     "Your streak is counting on you."),
    ("Rise and habit, {name} 🌞",
     "A quick tap and today's yours."),
    ("{name}, the best time is now 🎯",
     "Open your habits and check one off."),
    ("Good morning, {name} 👋",
     "Your daily habits are ready for you."),
]

# 8:30 PM — user has ≥1 incomplete habit today
_HABIT_EVENING_POOL = [
    ("{name}, a few habits still to go 🌙",
     "Quick check-in before the day ends."),
    ("Almost there, {name} ✅",
     "Finish your habits before midnight."),
    ("{name}, your habits await 🌿",
     "A few minutes and you're done for today."),
    ("End the day strong, {name} 💪",
     "Check off your remaining habits."),
    ("{name}, don't break the chain 🔥",
     "Complete today's habits to keep your streak."),
    ("Night check-in, {name} 🌛",
     "Your habits — quick and done."),
]

# Real-time — all habits completed for the day
_HABIT_PERFECT_DAY_POOL = [
    ("Perfect day, {name}! 🎉",
     "Every habit done. That's a big deal."),
    ("{name}, you crushed it today! 🏆",
     "All habits complete. Streak growing!"),
    ("100% today, {name} 🌟",
     "Perfect day logged. Keep it going!"),
    ("All done, {name}! ✨",
     "Every habit checked off. You showed up."),
]

# Real-time — habit streak milestones (3/7/14/21/30 days)
_HABIT_MILESTONE_POOL = [
    ("{name}, {streak} days straight! 🔥",
     "Your habit streak is on fire. Keep it up!"),
    ("{streak}-day streak, {name}! 🎯",
     "Consistency is your superpower."),
    ("{name}, {streak} days of showing up 🌱",
     "You're building something real."),
    ("Milestone: {streak} days, {name} 🏅",
     "That's real commitment. Be proud."),
]

# Sunday 8 PM — weekly progress summary
_WEEKLY_SUMMARY_POOL = [
    ("{name}, your week in review 📊",
     "{steps:,} steps · {habit_pct}% habits done. Nice week!"),
    ("Week done, {name}! 🎯",
     "{steps:,} steps and {habit_pct}% of habits checked off."),
    ("{name}, here's your weekly wrap 🌟",
     "Steps: {steps:,} · Habits: {habit_pct}% complete."),
]

# Daily rank change notifications
_RANK_UP_POOL = [
    ("{name}, up to rank #{rank}! 📈",
     "Climbed {moved} spot{s}. Keep the momentum!"),
    ("Rank #{rank} — nice move, {name} 🚀",
     "You climbed {moved} spot{s} today."),
]

_RANK_DOWN_POOL = [
    ("{name}, dropped to rank #{rank} 📉",
     "Down {moved} spot{s}. Today's a good day to push."),
    ("Rank #{rank} now, {name} — time to climb 💪",
     "Slipped {moved} spot{s}. Go get it back."),
]


# ─── 1. Evening step reminder (9 PM) ─────────────────────────────────────────

async def send_step_reminders(db: AsyncSession):
    """
    9 PM daily — nudge users who haven't logged any steps today.
    Caps:
      - Skips users with streak > 0  (they already got the 8 PM streak alert)
      - Inactive users (no steps for 3+ days) only nudged on Mon/Wed/Fri/Sun (≤4/week)
    """
    logger.info("JOB: evening step reminder")
    users = await _users_with_subscriptions(db)
    notified = 0
    for user in users:
        try:
            tz  = ZoneInfo(user.timezone or "Asia/Kolkata")
            now = datetime.now(tz)
            steps = await _steps_today(db, user.id, now.date())
            if steps > 0:
                continue  # already logged — no nudge
            streak = getattr(user, "global_current_streak", 0) or 0
            if streak > 0:
                continue  # streak users got the 8 PM alert — don't double-fire
            # Inactive cap: no steps in 3 days → only nudge on Mon/Wed/Fri/Sun
            active = await _had_steps_recently(db, user.id)
            if not active and not _is_nudge_day(now):
                continue
            if not await _try_claim_push_slot(db, user.id):
                continue
            name = (user.name or "there").split()[0]
            title_tpl, body_tpl = random.choice(_EVENING_POOL)
            subs = await _get_subscriptions(db, user.id)
            challenge_id = await _get_active_challenge_id(db, user.id)
            url = f"/socialapp/challanges/{challenge_id}/steps"
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name),
                "body":  body_tpl.format(name=name),
                "url":   url,
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
    8 PM daily — urgent nudge to streak holders who haven't logged yet.
    Inactive cap: if no steps in 3+ days, only fire on Mon/Wed/Fri/Sun.
    """
    logger.info("JOB: streak-at-risk check")
    users = await _users_with_subscriptions(db)
    notified = 0
    for user in users:
        try:
            streak = getattr(user, "global_current_streak", 0) or 0
            if streak == 0:
                continue
            tz  = ZoneInfo(user.timezone or "Asia/Kolkata")
            now = datetime.now(tz)
            steps = await _steps_today(db, user.id, now.date())
            if steps > 0:
                continue
            # Inactive cap (rare for streak holders, but guard anyway)
            active = await _had_steps_recently(db, user.id)
            if not active and not _is_nudge_day(now):
                continue
            if not await _try_claim_push_slot(db, user.id):
                continue
            name = (user.name or "there").split()[0]
            title_tpl, body_tpl = random.choice(_STREAK_RISK_POOL)
            subs = await _get_subscriptions(db, user.id)
            challenge_id = await _get_active_challenge_id(db, user.id)
            url = f"/socialapp/challanges/{challenge_id}/steps"
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name, streak=streak),
                "body":  body_tpl.format(name=name, streak=streak),
                "url":   url,
            })
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Streak-at-risk error for user {user.id}: {e}")
    logger.info(f"Streak-at-risk: notified {notified} users")
    return notified


# ─── 3. Smart challenge step nudges ─────────────────────────────────────────

async def send_challenge_step_nudges(db: AsyncSession):
    """
    Runs twice a day (noon + 4 PM) for every active challenge participant.

    Segments users into three buckets and sends a tailored notification
    that deep-links straight to /challenges/{id}/steps:

    A) COLD START   — 0 steps today, streak = 0  → friendly encouragement
    B) STREAK GUARD — 0 steps today, streak > 0  → urgent streak protection
    C) BELOW TARGET — logged some steps but < selected_daily_target → motivational push

    Users who already hit their daily target are skipped (no notification spam).
    """
    logger.info("JOB: challenge step nudges")
    today = date.today()
    notified = 0

    # One query: all active-challenge participants with push subscriptions
    # steps_today  = today only (trigger: has user hit daily target?)
    # total_steps  = full challenge period (shown in notification content)
    rows = await db.execute(text("""
        SELECT
            cp.user_id,
            cp.challenge_id,
            c.title                          AS challenge_title,
            c.start_date                     AS challenge_start,
            c.end_date                       AS challenge_end,
            cp.challenge_current_streak      AS streak,
            COALESCE(cp.selected_daily_target, 8000) AS daily_target,
            u.timezone,
            u.name                           AS user_name,
            COALESCE(SUM(CASE WHEN ds.day = :today THEN ds.steps ELSE 0 END), 0) AS steps_today,
            COALESCE(SUM(ds.steps), 0)       AS total_steps
        FROM challenge_participants cp
        JOIN challenges c  ON c.id  = cp.challenge_id
        JOIN users      u  ON u.id  = cp.user_id
        LEFT JOIN daily_steps ds
            ON ds.user_id = cp.user_id
            AND ds.day >= c.start_date
            AND ds.day <= :today
        WHERE c.status   = 'active'
          AND c.end_date  >= :today
          AND cp.left_at IS NULL
          AND EXISTS (
              SELECT 1 FROM push_subscriptions ps WHERE ps.user_id = cp.user_id
          )
        GROUP BY
            cp.user_id, cp.challenge_id, c.title, c.start_date, c.end_date,
            cp.challenge_current_streak,
            cp.selected_daily_target,
            u.timezone, u.name
    """), {"today": today})

    for row in rows.mappings():
        steps_today  = int(row["steps_today"])   # today only — used for trigger
        total_steps  = int(row["total_steps"])    # full challenge period — shown in notification
        target       = int(row["daily_target"])
        streak       = int(row["streak"])
        tz           = ZoneInfo(row["timezone"] or "Asia/Kolkata")
        now          = datetime.now(tz)
        url          = f"/socialapp/challanges/{row['challenge_id']}/steps"
        title        = None
        body         = None

        # Challenge-period completion % — vs steps expected by today (elapsed days × target)
        # Using elapsed days (not full duration) so "X% through challenge" reflects
        # how on-track the user is right now, not a tiny fraction of the full 30-day goal.
        challenge_start    = row["challenge_start"]
        challenge_end      = row["challenge_end"]
        elapsed_days       = max((today - challenge_start).days + 1, 1)
        expected_by_today  = target * elapsed_days
        pct                = round(total_steps / expected_by_today * 100) if expected_by_today > 0 else 0

        # Already hit daily target — no nudge
        if steps_today >= target:
            continue

        # Only nudge during daytime (8 AM – 8 PM user local time)
        if not (8 <= now.hour < 20):
            continue

        challenge = row['challenge_title']
        name      = (row['user_name'] or 'there').split()[0]

        # Inactive cap: no steps logged in 3+ days → only nudge on Mon/Wed/Fri/Sun
        active = await _had_steps_recently(db, row["user_id"])
        if not active and not _is_nudge_day(now):
            continue

        if steps_today == 0 and streak > 0:
            title, body = random.choice(_NUDGE_STREAK_POOL)
            title = title.format(name=name, streak=streak, challenge=challenge)
            body  = body.format(name=name, streak=streak, challenge=challenge)

        elif steps_today == 0:
            title, body = random.choice(_NUDGE_COLD_POOL)
            title = title.format(name=name, challenge=challenge)
            body  = body.format(name=name, challenge=challenge)

        elif steps_today < target:
            title, body = random.choice(_NUDGE_BELOW_POOL)
            title = title.format(name=name, steps=total_steps, pct=pct, challenge=challenge)
            body  = body.format(name=name, steps=total_steps, pct=pct, challenge=challenge)

        try:
            if not await _try_claim_push_slot(db, row["user_id"]):
                continue
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {"title": title, "body": body, "url": url})
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Challenge nudge error for user {row['user_id']}: {e}")

    logger.info(f"Challenge step nudges: notified {notified} users")
    return notified


# ─── 4. Habit morning reminder (7:30 AM) ─────────────────────────────────────

async def send_habit_morning_reminder(db: AsyncSession):
    """
    7:30 AM daily — remind users with an active habit challenge who haven't logged anything yet.
    """
    logger.info("JOB: habit morning reminder")
    today = date.today()
    notified = 0

    rows = await db.execute(text("""
        SELECT DISTINCT
            u.id         AS user_id,
            u.name,
            u.timezone,
            hc.id        AS challenge_id
        FROM users u
        JOIN habit_challenges hc ON hc.user_id = u.id
            AND hc.status   = 'active'
            AND hc.ends_at >= :today
        JOIN push_subscriptions ps ON ps.user_id = u.id
    """), {"today": today})

    for row in rows.mappings():
        try:
            done_row = await db.execute(text("""
                SELECT COUNT(*) AS done
                FROM daily_logs dl
                JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                WHERE hcm.challenge_id = :cid
                  AND dl.logged_date   = :today
                  AND dl.completed     = true
            """), {"cid": row["challenge_id"], "today": today})
            if (done_row.scalar() or 0) > 0:
                continue  # already logged something today
            if not await _try_claim_push_slot(db, row["user_id"]):
                continue
            name = (row["name"] or "there").split()[0]
            title_tpl, body_tpl = random.choice(_HABIT_MORNING_POOL)
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name),
                "body":  body_tpl.format(name=name),
                "url":   "/socialapp/habits",
            })
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Habit morning reminder error for user {row['user_id']}: {e}")

    logger.info(f"Habit morning reminder: notified {notified} users")
    return notified


# ─── 5. Habit evening nudge (8:30 PM) ────────────────────────────────────────

async def send_habit_evening_nudge(db: AsyncSession):
    """
    8:30 PM daily — nudge users who have ≥1 incomplete habit today.
    """
    logger.info("JOB: habit evening nudge")
    today = date.today()
    notified = 0

    rows = await db.execute(text("""
        SELECT DISTINCT
            u.id  AS user_id,
            u.name,
            u.timezone,
            hc.id AS challenge_id,
            (SELECT COUNT(*) FROM habit_commitments hcm2 WHERE hcm2.challenge_id = hc.id)
                AS total_habits,
            COALESCE((
                SELECT COUNT(*) FROM daily_logs dl
                JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                WHERE hcm.challenge_id = hc.id
                  AND dl.logged_date   = :today
                  AND dl.completed     = true
            ), 0) AS done_today
        FROM users u
        JOIN habit_challenges hc ON hc.user_id = u.id
            AND hc.status   = 'active'
            AND hc.ends_at >= :today
        JOIN push_subscriptions ps ON ps.user_id = u.id
    """), {"today": today})

    for row in rows.mappings():
        try:
            if row["done_today"] >= row["total_habits"]:
                continue  # all habits done — no nudge needed
            if not await _try_claim_push_slot(db, row["user_id"]):
                continue
            name = (row["name"] or "there").split()[0]
            title_tpl, body_tpl = random.choice(_HABIT_EVENING_POOL)
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name),
                "body":  body_tpl.format(name=name),
                "url":   "/socialapp/habits",
            })
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Habit evening nudge error for user {row['user_id']}: {e}")

    logger.info(f"Habit evening nudge: notified {notified} users")
    return notified


# ─── 6. Real-time: perfect day celebration ───────────────────────────────────

async def fire_habit_perfect_day(db: AsyncSession, user_id: str, challenge_id: int) -> None:
    """Fire a celebration push the moment a user completes every habit for today."""
    try:
        user_row = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_row.scalar_one_or_none()
        if not user:
            return
        name = (user.name or "there").split()[0]
        title_tpl, body_tpl = random.choice(_HABIT_PERFECT_DAY_POOL)
        subs = await _get_subscriptions(db, user_id)
        await _push_all(db, subs, {
            "title": title_tpl.format(name=name),
            "body":  body_tpl.format(name=name),
            "url":   "/socialapp/habits",
        })
        logger.info(f"Perfect-day push sent to user {user_id} (challenge {challenge_id})")
    except Exception as e:
        logger.error(f"fire_habit_perfect_day error for user {user_id}: {e}")


# ─── 7. Real-time: streak milestone ──────────────────────────────────────────

async def fire_habit_streak_milestone(db: AsyncSession, user_id: str, streak: int) -> None:
    """Fire a milestone push when a user's habit streak reaches 3/7/14/21/30 days."""
    try:
        user_row = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_row.scalar_one_or_none()
        if not user:
            return
        name = (user.name or "there").split()[0]
        title_tpl, body_tpl = random.choice(_HABIT_MILESTONE_POOL)
        subs = await _get_subscriptions(db, user_id)
        await _push_all(db, subs, {
            "title": title_tpl.format(name=name, streak=streak),
            "body":  body_tpl.format(name=name, streak=streak),
            "url":   "/socialapp/habits",
        })
        logger.info(f"Streak-milestone push ({streak} days) sent to user {user_id}")
    except Exception as e:
        logger.error(f"fire_habit_streak_milestone error for user {user_id}: {e}")


# ─── 8. Weekly summary (Sunday 8 PM) ─────────────────────────────────────────

async def send_weekly_summary(db: AsyncSession):
    """
    Sunday 8 PM — push a weekly recap: steps logged + habit completion % for the past 7 days.
    """
    logger.info("JOB: weekly summary")
    today = date.today()
    week_start = today - timedelta(days=6)
    notified = 0

    users = await _users_with_subscriptions(db)
    for user in users:
        try:
            steps_row = await db.execute(text("""
                SELECT COALESCE(SUM(steps), 0) AS weekly_steps
                FROM daily_steps
                WHERE user_id = :uid AND day >= :start AND day <= :today
            """), {"uid": str(user.id), "start": week_start, "today": today})
            weekly_steps = int(steps_row.scalar() or 0)

            habit_row = await db.execute(text("""
                SELECT
                    COUNT(DISTINCT hcm.id)                                    AS total_habits,
                    COALESCE(SUM(CASE WHEN dl.completed THEN 1 ELSE 0 END), 0) AS done_count
                FROM habit_challenges hc
                JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
                LEFT JOIN daily_logs dl
                    ON dl.commitment_id  = hcm.id
                    AND dl.logged_date  >= :start
                    AND dl.logged_date  <= :today
                WHERE hc.user_id = :uid AND hc.status = 'active'
            """), {"uid": str(user.id), "start": week_start, "today": today})
            h = habit_row.mappings().first()
            habit_pct = 0
            if h and h["total_habits"] > 0:
                expected = h["total_habits"] * 7
                habit_pct = round(h["done_count"] / expected * 100)

            if not await _try_claim_push_slot(db, user.id):
                continue
            name = (user.name or "there").split()[0]
            title_tpl, body_tpl = random.choice(_WEEKLY_SUMMARY_POOL)
            subs = await _get_subscriptions(db, user.id)
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name),
                "body":  body_tpl.format(name=name, steps=weekly_steps, habit_pct=habit_pct),
                "url":   "/socialapp",
            })
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Weekly summary error for user {user.id}: {e}")

    logger.info(f"Weekly summary: notified {notified} users")
    return notified


# ─── 9. Rank change notifications (daily after rank snapshot) ─────────────────

async def send_rank_change_notifications(db: AsyncSession):
    """
    Run after the 00:05 rank snapshot. Compares live rank vs previous_rank and
    pushes a rank-up or rank-down notification to users whose rank changed.
    """
    logger.info("JOB: rank change notifications")
    today = date.today()
    notified = 0

    challenges = await db.execute(
        text("SELECT id, start_date, end_date FROM challenges WHERE status = 'active'")
    )
    for ch in challenges.mappings().all():
        try:
            end_cap = min(ch["end_date"], today)

            live_ranks = await db.execute(text("""
                WITH totals AS (
                    SELECT cp.user_id,
                           COALESCE(SUM(ds.steps), 0) AS total_steps
                    FROM challenge_participants cp
                    LEFT JOIN daily_steps ds
                        ON ds.user_id = cp.user_id
                        AND ds.day   >= :start
                        AND ds.day   <= :today
                    WHERE cp.challenge_id = :cid AND cp.left_at IS NULL
                    GROUP BY cp.user_id
                )
                SELECT user_id,
                       ROW_NUMBER() OVER (ORDER BY total_steps DESC) AS rank
                FROM totals
            """), {"cid": ch["id"], "start": ch["start_date"], "today": end_cap})
            live_map = {str(r["user_id"]): int(r["rank"]) for r in live_ranks.mappings()}

            prev_rows = await db.execute(text("""
                SELECT user_id, previous_rank
                FROM challenge_participants
                WHERE challenge_id = :cid
                  AND left_at      IS NULL
                  AND previous_rank IS NOT NULL
            """), {"cid": ch["id"]})

            for row in prev_rows.mappings():
                uid = str(row["user_id"])
                prev_rank = int(row["previous_rank"])
                curr_rank = live_map.get(uid)
                if curr_rank is None or prev_rank == curr_rank:
                    continue

                moved = abs(prev_rank - curr_rank)
                went_up = curr_rank < prev_rank
                s = "" if moved == 1 else "s"

                user_row = await db.execute(select(User).where(User.id == uid))
                user = user_row.scalar_one_or_none()
                if not user:
                    continue

                if not await _try_claim_push_slot(db, uid):
                    continue
                name = (user.name or "there").split()[0]
                pool = _RANK_UP_POOL if went_up else _RANK_DOWN_POOL
                title_tpl, body_tpl = random.choice(pool)
                subs = await _get_subscriptions(db, uid)
                sent = await _push_all(db, subs, {
                    "title": title_tpl.format(name=name, rank=curr_rank, moved=moved, s=s),
                    "body":  body_tpl.format(name=name, rank=curr_rank, moved=moved, s=s),
                    "url":   f"/socialapp/challanges/{ch['id']}/steps",
                })
                if sent:
                    notified += 1
        except Exception as e:
            logger.error(f"Rank change error for challenge {ch['id']}: {e}")

    logger.info(f"Rank change notifications: notified {notified} users")
    return notified


# ─── TEST: Send all sample messages to a specific user ────────────────────────

_ALL_SAMPLE_MESSAGES = [
    # Evening pool (name only)
    *[{"pool": "EVENING", "title": t, "body": b} for t, b in _EVENING_POOL],
    # Streak risk pool (name + streak)
    *[{"pool": "STREAK_RISK", "title": t, "body": b} for t, b in _STREAK_RISK_POOL],
    # Nudge cold pool (name + challenge)
    *[{"pool": "NUDGE_COLD", "title": t, "body": b} for t, b in _NUDGE_COLD_POOL],
    # Nudge streak pool (name + streak + challenge)
    *[{"pool": "NUDGE_STREAK", "title": t, "body": b} for t, b in _NUDGE_STREAK_POOL],
    # Nudge below pool (name + pct + steps + target + remaining + challenge)
    *[{"pool": "NUDGE_BELOW", "title": t, "body": b} for t, b in _NUDGE_BELOW_POOL],
]

_TEST_USER_ID = "99b6b0eb-a343-4dc5-a646-baf035354c21"
_test_msg_index = 0  # global pointer — advances each run


async def send_service_started_notification(db: AsyncSession):
    """Sends a 'service started' push notification to the test user on every restart."""
    subs = await _get_subscriptions(db, _TEST_USER_ID)
    if not subs:
        logger.warning(f"STARTUP: no push subscriptions for test user {_TEST_USER_ID}")
        return
    from datetime import datetime
    ts = datetime.now().strftime("%d %b %Y %H:%M:%S")
    sent = await _push_all(db, subs, {
        "title": "✅ Service Started",
        "body":  f"Fitness Tracker API restarted successfully at {ts}.",
        "url":   "/socialapp",
    })
    logger.info(f"STARTUP: service-started notification sent={sent} to user {_TEST_USER_ID}")


async def send_test_notification_to_user(db: AsyncSession):
    """
    TEST ONLY — cycles through every sample message and sends one per run
    to the hard-coded test user. Remove this job from the scheduler when done.
    """
    global _test_msg_index
    subs = await _get_subscriptions(db, _TEST_USER_ID)
    if not subs:
        logger.warning(f"TEST: no push subscriptions for user {_TEST_USER_ID}")
        return

    # Fetch the user's actual name and active challenge (end_date must be today or future)
    user_row = await db.execute(text("""
        SELECT u.name, c.id AS challenge_id, c.title AS challenge_title
        FROM users u
        LEFT JOIN challenge_participants cp
            ON cp.user_id = u.id AND cp.left_at IS NULL
        LEFT JOIN challenges c
            ON c.id = cp.challenge_id
            AND c.status   = 'active'
            AND c.end_date >= CURRENT_DATE
        WHERE u.id = :user_id
        ORDER BY c.end_date ASC
        LIMIT 1
    """), {"user_id": _TEST_USER_ID})
    user_info      = user_row.mappings().first()
    user_name      = (user_info["name"] or "there").split()[0] if user_info else "there"
    challenge_id   = user_info["challenge_id"] if user_info and user_info["challenge_id"] else "unknown"
    challenge_name = user_info["challenge_title"] if user_info and user_info["challenge_title"] else "Test Challenge"
    url = f"/socialapp/challanges/{challenge_id}/steps"

    msg = _ALL_SAMPLE_MESSAGES[_test_msg_index % len(_ALL_SAMPLE_MESSAGES)]
    pool = msg["pool"]

    # Fill in all possible placeholders with real user name + challenge name + dummy values
    fmt = dict(
        name=user_name,
        streak=7,
        challenge=challenge_name,
        steps=62500,    # total steps in challenge period
        pct=78,         # challenge period completion %
    )
    title = msg["title"].format(**fmt)
    body  = msg["body"].format(**fmt)

    sent = await _push_all(db, subs, {"title": title, "body": body, "url": url})
    logger.info(
        f"TEST [{_test_msg_index + 1}/{len(_ALL_SAMPLE_MESSAGES)}] "
        f"pool={pool} sent={sent} | {title}"
    )
    _test_msg_index += 1
    if _test_msg_index >= len(_ALL_SAMPLE_MESSAGES):
        logger.info("TEST: all sample messages sent — cycle complete, resetting index.")
        _test_msg_index = 0
