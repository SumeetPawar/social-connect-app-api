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


async def _push_all(
    db: AsyncSession,
    subscriptions,
    message: dict,
    job: str = "unknown",
    user_id: str | None = None,
) -> int:
    """Fire notification to every device. Auto-deletes expired subscriptions.
    Logs every attempt to push_logs for delivery tracking.
    """
    sent = 0
    title = message.get("title", "")
    for sub in subscriptions:
        result, error_detail = send_web_push(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            message,
        )
        uid = user_id or str(sub.user_id)
        # Log every attempt regardless of outcome
        await db.execute(text("""
            INSERT INTO push_logs (user_id, job, result, title, endpoint_hash, error_detail)
            VALUES (:uid, :job, :result, :title, :ep, :err)
        """), {
            "uid": uid,
            "job": job,
            "result": result,
            "title": title,
            "ep": sub.endpoint[-12:] if sub.endpoint else None,
            "err": error_detail,
        })
        await db.commit()

        if result == PushResult.OK:
            sent += 1
        elif result == PushResult.EXPIRED:
            logger.info(f"Deleting expired subscription {sub.id}")
            await db.delete(sub)
            await db.commit()
        # PushResult.ERROR: already logged in push_logs + send_web_push warning
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
# Research principles applied:
#   • Identity-based framing (Atomic Habits) — "you're the kind of person who…"
#   • Self-compassion > shame — shame causes disengagement; compassion causes re-try
#   • Near-miss effect — show how close they are, not how far they fell
#   • Endowed progress — lead with what they already did, not what they missed
#   • Variable reward — large pools mean fresh messages every day
#   • Loss aversion (gentle) — protect what you've already built
#   • Implementation intention — name the specific tiny action
#   • Fresh-start effect — any new day is a clean slate
#   • Social proof — "consistent people / top performers" language
# Title ≤ 50 chars · Body ≤ 100 chars

# 9 PM — user hasn't logged any steps today
_EVENING_POOL = [
    # Identity preservation
    ("{name}, step people log on the quiet days too 👟",
     "That's what separates them. Tap and close."),
    # Tiny habit / BJ Fogg
    ("One tap, {name}. That's the whole job. 👍",
     "Log whatever you've got. No judgment."),
    # Self-compassion
    ("Be kind to yourself tonight, {name} 💛",
     "Rest days count. Log it and close the day."),
    # Progress not perfection
    ("{name}, a logged day beats a skipped one 🎯",
     "Consistency doesn't need perfection. Just log it."),
    # Endowed progress / curiosity
    ("{name}, what did your body do today? 🤔",
     "The steps happened. Log them and find out."),
    # Social proof
    ("People who log daily are 5× more consistent 📊",
     "{name}, log today's steps — even the small ones."),
    # Near-miss / permission
    ("{name}, you walked more than you think 🚶",
     "Stairs, errands, the commute — it all adds up."),
    # Fresh start / tonight
    ("{name}, end today on your terms 🌙",
     "Log it. Tomorrow starts clean."),
    # Future-self framing
    ("{name}, future-you reads this streak and smiles 🌱",
     "Even quiet days count. Log it."),
    # Gentle urgency
    ("Still a few hours left, {name} ⏳",
     "A short walk and a quick log — day done."),
    # Self-compassion
    ("Tired days are real, {name} 😌",
     "Log what you can. Every entry keeps the chain."),
    # Identity vote (Atomic Habits)
    ("{name}, every log is a vote for who you're becoming 🔥",
     "Cast today's vote. One tap."),
]

# 8 PM — user has an active streak but 0 steps today
_STREAK_RISK_POOL = [
    # Loss aversion (gentle) + specific action
    ("{name}, {streak} days — still yours to keep 🔥",
     "A 10-minute walk right now saves every one of them."),
    # Near-miss + tiny habit
    ("10 minutes, {name}. That's all 🚶",
     "Your {streak}-day streak is one short walk away from safety."),
    # Identity preservation
    ("{name}, your {streak}-day self doesn't quit tonight 💛",
     "One move, logged. Streak safe."),
    # Sunk cost + future reward
    ("{streak} days is too valuable to let go, {name} 🛡️",
     "Walk, log, protect. You've earned this."),
    # Empathy + urgency
    ("Still time tonight, {name} 🌙",
     "Streak intact = one short walk away."),
    # Progress identity
    ("{name}, {streak} days of showing up says it all 🌱",
     "Keep the story going before midnight."),
    # Tiny habit — implementation intention
    ("Walk to the end of the street, {name} 💎",
     "That's all. Log it. {streak}-day streak saved."),
    # Empathy + lowest bar possible
    ("Low energy tonight? Valid, {name} 😌",
     "A slow lap around the block still protects {streak} days."),
    # Identity commitment
    ("{name}, the {streak}-day version of you moves 💪",
     "Log before midnight and it's yours — forever."),
    # Fresh start framing
    ("{name}, tomorrow's streak day #{streak_plus_one} 🎯",
     "But only if you log tonight. You've got this."),
]

# Noon — challenge, 0 steps, no streak
_NUDGE_COLD_POOL = [
    # Tiny start / BJ Fogg
    ("{name}, even 500 steps gets you on the board 🌱",
     "The first log in {challenge} is the hardest. Then it flows."),
    # Identity — belonging
    ("You belong in {challenge}, {name} 💛",
     "No judgment. Show up with any number."),
    # Social proof
    ("Top performers in {challenge} start small, {name} 📊",
     "One short walk and you're one of them."),
    # Self-compassion
    ("Quiet day? That's okay, {name} 😌",
     "Any steps in {challenge} still move you forward."),
    # Near-miss trigger
    ("{name}, you're one walk away from the board 🎯",
     "Log anything in {challenge} and today's a win."),
    # Implementation intention
    ("Walk around the office once, {name} 🚶",
     "Log it in {challenge}. Smallest start, real momentum."),
    # Curiosity gap
    ("{name}, what if today surprised you? 🤔",
     "Open {challenge} and find out."),
    # Fresh start
    ("Today's a clean slate, {name} ✨",
     "One step in {challenge} changes everything."),
]

# Noon — challenge, 0 steps, streak > 0
_NUDGE_STREAK_POOL = [
    # Loss aversion + compassion
    ("{name}, protect {streak} days gently today 💛",
     "A small walk in {challenge} keeps every day safe."),
    # Tiny habit
    ("Low effort, big reward today, {name} 🌱",
     "A short walk shields {streak} days of work in {challenge}."),
    # Empathy + action
    ("{name}, be kind to yourself — then log it 😌",
     "A gentle walk in {challenge} still counts."),
    # Identity vote
    ("{name}, every step is a vote for your {streak}-day self 🎯",
     "Cast it in {challenge} before the day ends."),
    # Near-miss
    ("Don't let today be the gap, {name} 🛡️",
     "{streak} days in {challenge} — a slow walk keeps them."),
    # Progress
    ("{streak} days banked, {name} — protect the investment 💎",
     "Perfection not required in {challenge}. Just show up."),
    # Social proof
    ("Consistent {challenge} players protect their streaks, {name} 📈",
     "{streak} days down. One walk to keep it going."),
    # Implementation intention
    ("Walk around your floor once, {name} 🚶",
     "Log it in {challenge}. Streak saved. Done."),
]

# Noon — logged but below today's target
# vars: {name} {steps} {pct} {challenge} {remaining} {target}
_NUDGE_BELOW_POOL = [
    # Endowed progress — lead with what they did
    ("{name}, {pct}% there — you already started 🌱",
     "{steps:,} steps in {challenge}. {remaining:,} more closes the day."),
    # Near-miss — specific gap
    ("{remaining:,} steps to goal, {name} 🎯",
     "You're at {pct}% in {challenge}. That's about a 10-minute walk."),
    # Progress identity
    ("{name}, {pct}% and still moving in {challenge} 📊",
     "{steps:,} logged. The gap is smaller than it looks."),
    # Social proof
    ("Good effort so far, {name} 💛",
     "{pct}% in {challenge}. Finishers always close the gap."),
    # Curiosity — leaderboard hook
    ("{name}, a push now could move your rank 🤔",
     "{steps:,} steps in {challenge}. {remaining:,} more and goal's hit."),
    # Near-miss + achievable
    ("{remaining:,} steps and today's complete, {name} ✅",
     "You're {pct}% through {challenge}. The finish line is right there."),
    # Identity — closers
    ("You showed up — now close it, {name} 💪",
     "{steps:,} in {challenge}. {remaining:,} steps to complete the day."),
    # Reward framing — streak protection
    ("{name}, goal hit = streak protected 🔥",
     "{remaining:,} steps left in {challenge}. You can do this."),
]


# 7:30 AM — user has active habit challenge but nothing logged yet today
_HABIT_MORNING_POOL = [
    # Identity / Atomic Habits
    ("{name}, small habits compound into big change 🌅",
     "Start with one. The rest follow naturally."),
    # Implementation intention
    ("Morning, {name} — which habit first? 🌿",
     "Pick one. Start it. Day momentum begins."),
    # Social proof
    ("{name}, people who check habits before 9 AM stay 2× consistent 📊",
     "Tap in. Beat the morning."),
    # Curiosity / perfect day teaser
    ("{name}, today could be a perfect day 🌟",
     "One habit logged and you're already ahead."),
    # Tiny habit / lowest bar
    ("Rise and check, {name} 🌞",
     "Easiest win of the day is one tap away."),
    # Streak protection framing
    ("Good morning, {name} — your streak is watching 👋",
     "Log one habit before the day gets away."),
    # Identity
    ("{name}, habit people check in the morning 🎯",
     "You're one tap away from proving it again today."),
    # Fresh start
    ("Fresh day, fresh win, {name} ✨",
     "Tap one habit. Carry that feeling all day."),
    # Seed-to-tree
    ("{name}, every habit logged grows your tree 🌱",
     "Water it this morning. One tap is all it needs."),
    ("Your tree is waiting, {name} 🌿",
     "Log a habit and watch it grow. Day starts now."),
]

# 8:30 PM — user has ≥1 incomplete habit today
# vars: {name} {done} {remaining} {total}
_HABIT_EVENING_POOL = [
    # Endowed progress — lead with what they did
    ("{name}, {done} habit{ds} done — {remaining} left 🌙",
     "You've already done the hard part. Finish strong."),
    # Near-miss — almost perfect
    ("Almost a perfect day, {name} ✨",
     "{done} of {total} habits done. {remaining} more and tonight's complete."),
    # Compassion + action
    ("{name}, end the day proud 🌿",
     "{remaining} habit{rs} left. A few minutes and you're done."),
    # Curiosity / perfect day hook
    ("{name}, what if tonight's a perfect day? 🌟",
     "Finish {remaining} habit{rs} and find out."),
    # Identity
    ("Habit people finish, {name} 🔥",
     "{done} down, {remaining} to go. Close it strong."),
    # Streak protection
    ("{name}, tonight's habits protect tomorrow's streak 🛡️",
     "{remaining} left. Quick check-in before midnight."),
    # Tiny habit
    ("Two minutes, {name} 🌙",
     "Log {remaining} remaining habit{rs}. Close the day clean."),
    # Progress not perfection
    ("{name}, done beats perfect 🎯",
     "Finish what you can. {done} of {total} already done."),
    # Seed-to-tree
    ("{name}, your tree needs {remaining} more drop{rs} tonight 🌿",
     "Log the last habit{rs}. Don't leave it thirsty."),
]

# Real-time — all habits completed for the day
_HABIT_PERFECT_DAY_POOL = [
    # Big celebration + identity
    ("Perfect day, {name}! 🎉",
     "Every habit done. That's exactly who you're becoming."),
    # Identity + streak
    ("{name}, 100% — that's your standard now 🏆",
     "A perfect day. Your streak just got stronger."),
    # Social proof + rarity
    ("You hit 100% today, {name} 🌟",
     "Most people don't. You did. Streak growing."),
    # Anticipation / investment
    ("All done, {name}! ✨",
     "Every habit checked. See you tomorrow for another."),
    # Atomic Habits identity
    ("{name}, this is what commitment looks like 💎",
     "Perfect day. The habit is becoming automatic."),
    # Near-future milestone teaser
    ("Flawless, {name}! 🔥",
     "All habits done. One more perfect day builds the streak."),
    # Seed-to-tree
    ("{name}, your tree grew today 🌳",
     "Every habit done. That's what a perfect day looks like."),
]

# Real-time — habit streak milestones (3/7/14/21/30 days)
_HABIT_MILESTONE_POOL = [
    # Science hook — builds belief
    ("{name}, {streak} days — habits start becoming automatic 🔥",
     "Research says this is where it sticks. Keep the chain."),
    # Identity
    ("{streak}-day streak, {name} 🎯",
     "Consistency is becoming your personality. Don't stop."),
    # Progress + what's next
    ("{name}, {streak} days of showing up 🌱",
     "You're building something that lasts. What does {streak} more look like?"),
    # Celebration + curiosity
    ("Milestone: {streak} days, {name}! 🏅",
     "Real commitment. The next milestone is closer than you think."),
    # Near-future teaser
    ("{name}, {streak} days straight 💪",
     "Habits this consistent become who you are. Keep it going."),
    # Seed-to-tree — streak as tree growth
    ("{name}, {streak} days — your tree is growing 🌳",
     "Roots go deeper every day you show up. Keep watering it."),
]

# Sunday 8 PM — weekly progress summary
_WEEKLY_SUMMARY_POOL = [
    # Endowed progress framing
    ("{name}, your week in numbers 📊",
     "{steps:,} steps · {habit_pct}% habits done. That's a real week."),
    # Identity reinforcement
    ("Week wrapped, {name} — you showed up 🎯",
     "{steps:,} steps and {habit_pct}% habits. That's what consistency looks like."),
    # Anticipation hook for next week
    ("{name}, week in review 🌟",
     "{steps:,} steps · {habit_pct}% habits. Next week: beat this."),
    # Self-compassion (good for low-score weeks)
    ("{name}, every week teaches you something 💛",
     "{steps:,} steps · {habit_pct}% habits done. Fresh week starts tomorrow."),
    # Progress compounds
    ("{name}, here's what this week built 🌱",
     "{steps:,} steps · {habit_pct}% habits. Small weeks compound into big months."),
]

# Rank went up
_RANK_UP_POOL = [
    # Celebration + momentum
    ("{name}, up to rank #{rank}! 🚀",
     "Climbed {moved} spot{s}. Consistency got you here — keep it going."),
    # Identity + near-future milestone
    ("Rank #{rank} — you earned it, {name} 📈",
     "Up {moved} spot{s}. The next rank is within reach."),
    # Curiosity + next target
    ("{name}, rank #{rank} now 🏅",
     "+{moved} spot{s} today. Stay consistent and it keeps moving."),
    # Social proof
    ("Moving up the board, {name} 💪",
     "Rank #{rank} — {moved} spot{s} climbed. This is what daily effort looks like."),
]

# Rank went down — most critical pool, must never shame or demotivate
_RANK_DOWN_POOL = [
    # Reframe as temporary + specific path forward
    ("{name}, rank #{rank} right now 💛",
     "Down {moved} spot{s}. One strong day is all it takes to climb back."),
    # Self-compassion + "this happens"
    ("Rankings shift daily, {name} 💎",
     "You're at #{rank} now. Consistent days always bring you back up."),
    # Near-miss — frame the gap, not the drop
    ("{name}, rank #{rank} — and climbing back is simple 🎯",
     "{moved} spot{s} down. Walk your target today and watch it flip."),
    # Identity protection — consistent people recover
    ("Temporary dip, {name} 🌱",
     "Rank #{rank} right now. The players who stay consistent always rise."),
]

# Habit 7-day cycle completion
_HABIT_CYCLE_POOL = [
    # Full celebration
    ("7-day cycle complete, {name}! 🎉",
     "{habit_pct}% habits done · {perfect_days} perfect day{ps}. That's a real win."),
    # Identity + stats
    ("{name}, your habit week is wrapped 🏁",
     "{habit_pct}% across 7 days · {perfect_days} perfect day{ps}. You're building the person you want to be."),
    # Progress not perfection (works for any %)
    ("Cycle done, {name} 💪",
     "{habit_pct}% — {done_days} of {possible_days} habits logged · {perfect_days} perfect day{ps}. Every cycle you level up."),
    # Anticipation hook for next cycle
    ("{name}, 7 days in the books 📅",
     "{habit_pct}% this cycle · {perfect_days} perfect day{ps}. Next cycle: same habits, better score."),
    # Self-compassion (good for tough weeks)
    ("{name}, you showed up for 7 days 💛",
     "{done_days} habits logged out of {possible_days} · {perfect_days} perfect day{ps}. That matters."),
    # Seed-to-tree — cycle as harvest
    ("{name}, 7 days of watering your tree 🌳",
     "{habit_pct}% habits done · {perfect_days} perfect day{ps}. The roots are stronger than last cycle."),
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
            }, job="step_reminder", user_id=str(user.id))
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
                "title": title_tpl.format(name=name, streak=streak, streak_plus_one=streak + 1),
                "body":  body_tpl.format(name=name, streak=streak, streak_plus_one=streak + 1),
                "url":   url,
            }, job="streak_at_risk", user_id=str(user.id))
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
            remaining = max(target - steps_today, 0)
            title, body = random.choice(_NUDGE_BELOW_POOL)
            title = title.format(name=name, steps=total_steps, pct=pct, challenge=challenge, remaining=remaining, target=target)
            body  = body.format(name=name, steps=total_steps, pct=pct, challenge=challenge, remaining=remaining, target=target)

        try:
            if not await _try_claim_push_slot(db, row["user_id"]):
                continue
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {"title": title, "body": body, "url": url}, job="challenge_nudge", user_id=str(row["user_id"]))
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
            }, job="habit_morning_reminder", user_id=str(row["user_id"]))
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
            done_count = int(row["done_today"])
            total_count = int(row["total_habits"])
            remaining_count = total_count - done_count
            ds = "" if done_count == 1 else "s"       # "habit" vs "habits"
            rs = "" if remaining_count == 1 else "s"
            title_tpl, body_tpl = random.choice(_HABIT_EVENING_POOL)
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name, done=done_count, remaining=remaining_count, total=total_count, ds=ds, rs=rs),
                "body":  body_tpl.format(name=name, done=done_count, remaining=remaining_count, total=total_count, ds=ds, rs=rs),
                "url":   "/socialapp/habits",
            }, job="habit_evening_nudge", user_id=str(row["user_id"]))
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
        from app.services.notification_service import write_inbox
        user_row = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_row.scalar_one_or_none()
        if not user:
            return
        name = (user.name or "there").split()[0]
        title_tpl, body_tpl = random.choice(_HABIT_PERFECT_DAY_POOL)
        push_title = title_tpl.format(name=name)
        push_body  = body_tpl.format(name=name)
        subs = await _get_subscriptions(db, user_id)
        await _push_all(db, subs, {
            "title": push_title,
            "body":  push_body,
            "url":   "/socialapp/habits",
        }, job="perfect_day", user_id=str(user_id))
        # Write to inbox — achievements never expire
        await write_inbox(
            db,
            user_id=str(user_id),
            type="perfect_day",
            template_key="perfect_day_v1",
            payload={"name": name},
            action_url="/socialapp/habits",
            push_title=push_title,
            push_body=push_body,
        )
        await db.commit()
        logger.info(f"Perfect-day push sent to user {user_id} (challenge {challenge_id})")
    except Exception as e:
        logger.error(f"fire_habit_perfect_day error for user {user_id}: {e}")


# ─── 7. Real-time: streak milestone ──────────────────────────────────────────

async def fire_habit_streak_milestone(db: AsyncSession, user_id: str, streak: int) -> None:
    """Fire a milestone push when a user's habit streak reaches 3/7/14/21/30 days."""
    try:
        from app.services.notification_service import write_inbox
        user_row = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_row.scalar_one_or_none()
        if not user:
            return
        name = (user.name or "there").split()[0]
        title_tpl, body_tpl = random.choice(_HABIT_MILESTONE_POOL)
        push_title = title_tpl.format(name=name, streak=streak)
        push_body  = body_tpl.format(name=name, streak=streak)
        subs = await _get_subscriptions(db, user_id)
        await _push_all(db, subs, {
            "title": push_title,
            "body":  push_body,
            "url":   "/socialapp/habits",
        }, job="streak_milestone", user_id=str(user_id))
        # Write to inbox — milestones never expire
        await write_inbox(
            db,
            user_id=str(user_id),
            type="habit_milestone",
            template_key="habit_milestone_v1",
            payload={"name": name, "streak": streak},
            action_url="/socialapp/habits",
            push_title=push_title,
            push_body=push_body,
        )
        await db.commit()
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
            push_title = title_tpl.format(name=name)
            push_body  = body_tpl.format(name=name, steps=weekly_steps, habit_pct=habit_pct)
            subs = await _get_subscriptions(db, user.id)
            sent = await _push_all(db, subs, {
                "title": push_title,
                "body":  push_body,
                "url":   "/socialapp",
            }, job="weekly_summary", user_id=str(user.id))
            if sent:
                notified += 1
            # Write to inbox regardless of push delivery
            from app.services.notification_service import write_inbox
            await write_inbox(
                db,
                user_id=str(user.id),
                type="weekly_summary",
                template_key="weekly_summary_v1",
                payload={"name": name, "steps": weekly_steps, "habit_pct": habit_pct},
                action_url="/socialapp",
                push_title=push_title,
                push_body=push_body,
            )
            await db.commit()
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
                push_title = title_tpl.format(name=name, rank=curr_rank, moved=moved, s=s)
                push_body  = body_tpl.format(name=name, rank=curr_rank, moved=moved, s=s)
                subs = await _get_subscriptions(db, uid)
                sent = await _push_all(db, subs, {
                    "title": push_title,
                    "body":  push_body,
                    "url":   f"/socialapp/challanges/{ch['id']}/steps",
                }, job="rank_change", user_id=str(uid))
                if sent:
                    notified += 1
                # Only rank-up goes into inbox (rank-down is transient/demotivating to keep visible)
                if went_up:
                    from app.services.notification_service import write_inbox
                    await write_inbox(
                        db,
                        user_id=uid,
                        type="rank_up",
                        template_key="rank_up_v1",
                        payload={"name": name, "rank": curr_rank, "moved": moved},
                        action_url=f"/socialapp/challanges/{ch['id']}/steps",
                        push_title=push_title,
                        push_body=push_body,
                    )
                    await db.commit()
        except Exception as e:
            logger.error(f"Rank change error for challenge {ch['id']}: {e}")

    logger.info(f"Rank change notifications: notified {notified} users")
    return notified


# ─── 10. Habit cycle completion summary (daily, fires when ends_at = today) ───

async def send_habit_cycle_summary(db: AsyncSession):
    """
    Runs daily at 21:00 IST.
    Finds every active habit_challenge whose ends_at = today, computes 7-day
    completion stats, sends a celebratory push, then marks the challenge completed.
    """
    logger.info("JOB: habit cycle summary")
    today = date.today()
    notified = 0

    # All active challenges that end today
    ending = await db.execute(text("""
        SELECT hc.id AS challenge_id, hc.user_id, hc.started_at,
               u.name AS user_name
        FROM habit_challenges hc
        JOIN users u ON u.id = hc.user_id
        WHERE hc.ends_at = :today AND hc.status = 'active'
    """), {"today": today})
    rows = ending.mappings().all()

    if not rows:
        logger.info("Habit cycle summary: no challenges ending today")
        return 0

    for row in rows:
        uid = str(row["user_id"])
        challenge_id = int(row["challenge_id"])
        started_at = row["started_at"]
        try:
            # ── 7-day completion stats ────────────────────────────────────
            stats_row = await db.execute(text("""
                SELECT
                    COUNT(DISTINCT hcm.id)                                         AS total_habits,
                    COALESCE(SUM(CASE WHEN dl.completed THEN 1 ELSE 0 END), 0)     AS done_count
                FROM habit_commitments hcm
                LEFT JOIN daily_logs dl
                    ON  dl.commitment_id = hcm.id
                    AND dl.logged_date   >= :start
                    AND dl.logged_date   <= :today
                WHERE hcm.challenge_id = :cid
            """), {"cid": challenge_id, "start": started_at, "today": today})
            s = stats_row.mappings().first() or {}
            total_habits = int(s.get("total_habits") or 0)
            done_count   = int(s.get("done_count")   or 0)
            possible     = total_habits * 7
            habit_pct    = round(done_count / possible * 100) if possible else 0

            # Perfect days: days where every habit was completed
            perfect_row = await db.execute(text("""
                SELECT COUNT(*) AS perfect_days
                FROM (
                    SELECT dl.logged_date
                    FROM habit_commitments hcm
                    JOIN daily_logs dl
                        ON  dl.commitment_id = hcm.id
                        AND dl.logged_date   >= :start
                        AND dl.logged_date   <= :today
                        AND dl.completed
                    WHERE hcm.challenge_id = :cid
                    GROUP BY dl.logged_date
                    HAVING COUNT(*) = :total_habits
                ) t
            """), {"cid": challenge_id, "start": started_at, "today": today,
                   "total_habits": total_habits})
            perfect_days = int(perfect_row.scalar() or 0)

            # ── Push notification ─────────────────────────────────────────
            if not await _try_claim_push_slot(db, uid):
                logger.debug(f"Cycle summary skipped (cap): user {uid}")
            else:
                name = (row["user_name"] or "there").split()[0]
                ps = "" if perfect_days == 1 else "s"
                title_tpl, body_tpl = random.choice(_HABIT_CYCLE_POOL)
                push_title = title_tpl.format(name=name)
                push_body  = body_tpl.format(
                    name=name,
                    habit_pct=habit_pct,
                    perfect_days=perfect_days,
                    ps=ps,
                    done_days=done_count,
                    possible_days=possible,
                )
                subs = await _get_subscriptions(db, uid)
                sent = await _push_all(db, subs, {
                    "title": push_title,
                    "body":  push_body,
                    "url":   "/socialapp/habits",
                }, job="habit_cycle_summary", user_id=str(uid))
                if sent:
                    notified += 1
                # Write to inbox — cycle summaries kept 90 days
                from app.services.notification_service import write_inbox
                await write_inbox(
                    db,
                    user_id=str(uid),
                    type="habit_cycle",
                    template_key="habit_cycle_v1",
                    payload={
                        "name":         name,
                        "habit_pct":    habit_pct,
                        "perfect_days": perfect_days,
                        "done_days":    done_count,
                        "possible_days": possible,
                    },
                    action_url="/socialapp/habits",
                    push_title=push_title,
                    push_body=push_body,
                )

            # ── Mark challenge completed ──────────────────────────────────
            await db.execute(text("""
                UPDATE habit_challenges
                SET status = 'completed'
                WHERE id = :cid
            """), {"cid": challenge_id})
            await db.commit()

        except Exception as e:
            await db.rollback()
            logger.error(f"Habit cycle summary error for challenge {challenge_id}: {e}")

    logger.info(f"Habit cycle summary: notified {notified} users, processed {len(rows)} challenges")
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
    }, job="service_startup", user_id=str(_TEST_USER_ID))
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

    sent = await _push_all(db, subs, {"title": title, "body": body, "url": url}, job="test_notification", user_id=str(_TEST_USER_ID))
    logger.info(
        f"TEST [{_test_msg_index + 1}/{len(_ALL_SAMPLE_MESSAGES)}] "
        f"pool={pool} sent={sent} | {title}"
    )
    _test_msg_index += 1
    if _test_msg_index >= len(_ALL_SAMPLE_MESSAGES):
        logger.info("TEST: all sample messages sent — cycle complete, resetting index.")
        _test_msg_index = 0


# ─── 11. Body scan reminder (8:00 AM daily) ──────────────────────────────────
#
# Decision rules:
#   days_since = today - last_scan_date
#   days_since == 14          → "Time to measure" (first reminder, exact day)
#   days_since  > 14
#     AND (days_since - 14) % 3 == 0  → "You're overdue" (every 3 days until they scan)
#
# This means a user who never scans again gets nudged on day 14, 17, 20, 23 ...
# Capped at 60 days since last scan to avoid pestering long-inactive users.

_BODY_SCAN_DUE_POOL = [
    ("Time to check in 📊", "It's been 2 weeks — a quick scan will show how your habits are moving the numbers."),
    ("Ready for your scan? 📊", "2 weeks is the sweet spot. Log your body metrics today to track real progress."),
    ("Scan day 📊", "Your last scan was 14 days ago. 2 minutes and you'll see exactly where you stand."),
]

_BODY_SCAN_OVERDUE_POOL = [
    ("Your body scan is overdue", "It's been a while since your last scan. Grab your scale and log your metrics today."),
    ("Missing your scan data 📊", "Trends only show up when you measure. Log your body metrics — it takes 2 minutes."),
    ("Check in on your progress", "Your habits are working — find out how in your body metrics scan."),
]


async def send_body_scan_reminders(db: AsyncSession):
    """
    8:00 AM daily.
    Notifies users who are due (14 days) or overdue (>14 days, every 3-day interval up to 60 days)
    for their next body composition scan.
    """
    logger.info("JOB: body scan reminder")
    today = date.today()
    notified = 0

    rows = await db.execute(text("""
        SELECT
            u.id              AS user_id,
            u.name,
            MAX(bm.recorded_date) AS last_scan
        FROM users u
        JOIN body_metrics bm ON bm.user_id = u.id
        JOIN push_subscriptions ps ON ps.user_id = u.id
        GROUP BY u.id, u.name
        HAVING MAX(bm.recorded_date) < :today
    """), {"today": today})

    for row in rows.mappings():
        try:
            days_since = (today - row["last_scan"]).days
            if days_since > 60:
                continue  # too long inactive — stop pestering
            if days_since == 22:
                pool = _BODY_SCAN_DUE_POOL
            elif days_since > 22 and (days_since - 22) % 3 == 0:
                pool = _BODY_SCAN_OVERDUE_POOL
            else:
                continue  # not a reminder day

            if not await _try_claim_push_slot(db, row["user_id"]):
                continue

            name = (row["name"] or "there").split()[0]
            title_tpl, body_tpl = random.choice(pool)
            subs = await _get_subscriptions(db, row["user_id"])
            sent = await _push_all(db, subs, {
                "title": title_tpl.format(name=name),
                "body":  body_tpl.format(name=name),
                "url":   "/socialapp/body-metrics",
            }, job="body_scan_reminder", user_id=str(row["user_id"]))
            if sent:
                notified += 1
        except Exception as e:
            logger.error(f"Body scan reminder error for user {row['user_id']}: {e}")

    logger.info(f"Body scan reminder: notified {notified} users")
    return notified


# ─── Weekly partner rotation ──────────────────────────────────────────────────

_IST = ZoneInfo("Asia/Kolkata")


async def send_partner_keep_or_change_prompts(db: AsyncSession) -> int:
    """
    Friday 08:00 IST job — send Keep/Change vote prompts to all active auto-managed pairs.
    Sets keep_deadline = Sunday 23:59 IST on each pair.
    """
    from app.services.notification_service import write_inbox

    # Sunday 23:59 IST this week
    now_ist = datetime.now(_IST)
    days_until_sunday = (6 - now_ist.weekday()) % 7 or 7
    sunday = now_ist.replace(hour=23, minute=59, second=0, microsecond=0) + timedelta(days=days_until_sunday)
    # Convert to UTC for storage
    sunday_utc = sunday.astimezone(ZoneInfo("UTC"))

    pairs = (await db.execute(text("""
        SELECT ap.id, ap.requester_id, ap.partner_id,
               u1.name AS req_name, u2.name AS par_name
        FROM   accountability_partners ap
        JOIN   users u1 ON u1.id = ap.requester_id
        JOIN   users u2 ON u2.id = ap.partner_id
        WHERE  ap.status = 'approved'
          AND  ap.week_start IS NOT NULL
          AND  (ap.keep_deadline IS NULL OR ap.keep_deadline < now())
    """))).mappings().all()

    notified = 0
    for p in pairs:
        await db.execute(text("""
            UPDATE accountability_partners
            SET keep_deadline  = :dl,
                requester_keep = NULL,
                partner_keep   = NULL
            WHERE id = :pid
        """), {"dl": sunday_utc, "pid": p["id"]})

        for uid, partner_name in (
            (str(p["requester_id"]), (p["par_name"] or "your partner").split()[0]),
            (str(p["partner_id"]),   (p["req_name"] or "your partner").split()[0]),
        ):
            subs = await _get_subscriptions(db, uid)
            await _push_all(db, subs, {
                "title": "Keep or change your partner?",
                "body":  f"Your week with {partner_name} ends Sunday. Tap to vote.",
                "url":   f"/socialapp/partners",
            }, job="partner_keep_vote", user_id=uid)

            await write_inbox(
                db,
                user_id=uid,
                type="partner_keep_vote",
                template_key="partner_keep_vote_v1",
                payload={"partner_name": partner_name, "pair_id": p["id"]},
                action_url="/socialapp/partners",
            )
        notified += 1

    await db.commit()
    logger.info("Partner keep-vote prompts sent for %d pairs", notified)
    return notified


async def run_weekly_partner_rotation(db: AsyncSession) -> int:
    """
    Monday 07:00 IST job — rotate or renew partner pairs.

    For each pair where keep_deadline has passed:
      - Both voted keep=True  → renew (update week_start, reset votes)
      - Any other outcome     → complete old pair, assign new partner from dept active pool
    """
    from app.services.notification_service import write_inbox
    from sqlalchemy import select as sa_select
    import random as _random

    today = date.today()

    expired_pairs = (await db.execute(text("""
        SELECT ap.id,
               ap.requester_id, ap.partner_id,
               ap.requester_keep, ap.partner_keep,
               u1.department_id AS dept_id,
               u1.name AS req_name, u2.name AS par_name
        FROM   accountability_partners ap
        JOIN   users u1 ON u1.id = ap.requester_id
        JOIN   users u2 ON u2.id = ap.partner_id
        WHERE  ap.status = 'approved'
          AND  ap.keep_deadline IS NOT NULL
          AND  ap.keep_deadline < now()
    """))).mappings().all()

    rotated = 0

    for p in expired_pairs:
        both_keep = p["requester_keep"] is True and p["partner_keep"] is True

        if both_keep:
            # Renew: reset votes and push week forward
            await db.execute(text("""
                UPDATE accountability_partners
                SET week_start     = :ws,
                    requester_keep = NULL,
                    partner_keep   = NULL,
                    keep_deadline  = NULL
                WHERE id = :pid
            """), {"ws": today, "pid": p["id"]})

            for uid, partner_name in (
                (str(p["requester_id"]), (p["par_name"] or "partner").split()[0]),
                (str(p["partner_id"]),   (p["req_name"] or "partner").split()[0]),
            ):
                subs = await _get_subscriptions(db, uid)
                await _push_all(db, subs, {
                    "title": f"Continuing with {partner_name}!",
                    "body":  "You both voted to keep going. Let's have a great week!",
                    "url":   "/socialapp/partners",
                }, job="partner_renewed", user_id=uid)
                await write_inbox(
                    db, user_id=uid, type="partner_renewed",
                    template_key="partner_renewed_v1",
                    payload={"partner_name": partner_name},
                    action_url="/socialapp/partners",
                )
        else:
            # Rotate: mark old pair completed, assign new partner
            await db.execute(text("""
                UPDATE accountability_partners SET status = 'completed' WHERE id = :pid
            """), {"pid": p["id"]})
            await db.execute(text("""
                UPDATE partner_messages
                SET expires_at = now() + INTERVAL '30 days'
                WHERE pair_id = :pid AND expires_at IS NULL
            """), {"pid": p["id"]})

            dept_id = str(p["dept_id"])
            # Find active users in dept not already paired, excluding the current pair members
            active_candidates = (await db.execute(text("""
                SELECT u.id, u.name
                FROM users u
                WHERE u.department_id = :dept
                  AND u.id NOT IN (:req, :par)
                  AND u.id NOT IN (
                      SELECT requester_id FROM accountability_partners WHERE status = 'approved'
                      UNION
                      SELECT partner_id   FROM accountability_partners WHERE status = 'approved'
                  )
                  AND (
                      EXISTS (SELECT 1 FROM daily_steps ds WHERE ds.user_id = u.id AND ds.day >= current_date - 7)
                      OR EXISTS (
                          SELECT 1 FROM daily_logs dl
                          JOIN   habit_commitments hcm ON hcm.id = dl.commitment_id
                          JOIN   habit_challenges  hc  ON hc.id  = hcm.challenge_id
                          WHERE  hc.user_id = u.id AND dl.logged_date >= current_date - 7
                      )
                      OR u.created_at >= now() - INTERVAL '7 days'
                  )
                ORDER BY random()
                LIMIT 1
            """), {"dept": dept_id, "req": str(p["requester_id"]), "par": str(p["partner_id"])})).mappings().all()

            # Re-pair each user from the old pair
            for uid, old_partner_name in (
                (str(p["requester_id"]), (p["par_name"] or "partner").split()[0]),
                (str(p["partner_id"]),   (p["req_name"] or "partner").split()[0]),
            ):
                new_candidate = active_candidates[0] if active_candidates else None

                if new_candidate:
                    new_partner_id   = str(new_candidate["id"])
                    new_partner_name = (new_candidate["name"] or "Someone").split()[0]

                    await db.execute(text("""
                        INSERT INTO accountability_partners
                            (requester_id, partner_id, status, assignment_type, approved_at, week_start)
                        VALUES (:a, :b, 'approved', 'auto', now(), :ws)
                        ON CONFLICT (requester_id, partner_id) DO UPDATE
                            SET status = 'approved', assignment_type = 'auto',
                                approved_at = now(), week_start = :ws
                    """), {"a": uid, "b": new_partner_id, "ws": today})

                    subs = await _get_subscriptions(db, uid)
                    await _push_all(db, subs, {
                        "title": f"Meet your new partner: {new_partner_name}!",
                        "body":  "Your accountability partner for this week is ready. Say hi!",
                        "url":   "/socialapp/partners",
                    }, job="partner_rotated", user_id=uid)
                    await write_inbox(
                        db, user_id=uid, type="partner_rotated",
                        template_key="partner_rotated_v1",
                        payload={"partner_name": new_partner_name, "partner_id": new_partner_id},
                        action_url="/socialapp/partners",
                    )
                else:
                    # No available partner — notify admin via inbox (best effort)
                    logger.warning("No available partner for user %s after rotation", uid)

        rotated += 1

    await db.commit()
    logger.info("Weekly partner rotation: processed %d pairs", rotated)
    return rotated


async def cleanup_expired_partner_messages(db: AsyncSession) -> int:
    """Nightly job — delete partner_messages where expires_at < now()."""
    result = await db.execute(text("""
        DELETE FROM partner_messages WHERE expires_at < now()
        RETURNING id
    """))
    deleted = len(result.fetchall())
    await db.commit()
    logger.info("Cleaned up %d expired partner messages", deleted)
    return deleted
