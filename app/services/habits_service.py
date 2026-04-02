from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models import HabitChallenge, ChallengeStatus, DailyLog, Habit, HabitCommitment
from app.schemas.habits import ChallengeCreate
from app.services.reminder_service import fire_habit_perfect_day, fire_habit_streak_milestone

_STREAK_MILESTONES = {3, 7, 14, 21, 30}
async def _get_habit(db: AsyncSession, slug: str) -> Habit:
    result = await db.execute(select(Habit).where(Habit.slug == slug))
    h = result.scalar_one_or_none()
    if not h:
        raise HTTPException(404, f"Habit '{slug}' not found")
    return h


async def create_challenge(db: AsyncSession, user_id: str, body: ChallengeCreate) -> HabitChallenge:
    # Abandon any existing active challenge
    result = await db.execute(
        select(HabitChallenge).where(
            HabitChallenge.user_id == user_id,
            HabitChallenge.status == ChallengeStatus.active,
        )
    )
    for existing in result.scalars().all():
        existing.status = ChallengeStatus.abandoned

    today = date.today()
    challenge = HabitChallenge(
        user_id=user_id,
        pack_id=body.pack_id,
        started_at=today,
        ends_at=today + timedelta(days=20),
    )
    db.add(challenge)
    await db.flush()

    for order, slug in enumerate(body.habit_slugs):
        habit = await _get_habit(db, slug)
        db.add(HabitCommitment(challenge_id=challenge.id, habit_id=habit.id, sort_order=order))

    await db.commit()
    await db.refresh(challenge)
    return challenge


async def get_active_challenge(db: AsyncSession, user_id: str) -> HabitChallenge:
    result = await db.execute(
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.habit),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.logs),
        )
        .where(
            HabitChallenge.user_id == user_id,
            HabitChallenge.status == ChallengeStatus.active,
        )
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "No active challenge")
    return c


async def get_today(db: AsyncSession, user_id: str, target_date: date | None = None) -> dict:
    challenge = await get_active_challenge(db, user_id)
    today = target_date or date.today()
    habits_today = []
    for c in sorted(challenge.commitments, key=lambda x: x.sort_order):
        log = next((l for l in c.logs if l.logged_date == today), None)
        habits_today.append({
            "commitment_id": c.id,
            "habit": c.habit,
            "completed": log.completed if log else False,
            "value": log.value if log else None,
            "log_id": log.id if log else None,
        })
    return {
        "challenge_id": challenge.id,
        "date": today,
        "day_number": (today - challenge.started_at).days + 1,
        "habits": habits_today,
        "completed_count": sum(1 for h in habits_today if h["completed"]),
        "total_count": len(habits_today),
    }


async def upsert_log(db: AsyncSession, user_id: str, commitment_id: int,
                     logged_date: date, completed: bool, value: int | None) -> dict:
    result = await db.execute(
        select(HabitCommitment)
        .join(HabitChallenge)
        .where(HabitCommitment.id == commitment_id, HabitChallenge.user_id == user_id)
    )
    commitment = result.scalar_one_or_none()
    if not commitment:
        raise HTTPException(404, "Commitment not found")

    log_result = await db.execute(
        select(DailyLog).where(
            DailyLog.commitment_id == commitment_id,
            DailyLog.logged_date == logged_date,
        )
    )
    log = log_result.scalar_one_or_none()

    if log:
        log.completed = completed
        log.value = value
    else:
        log = DailyLog(commitment_id=commitment_id, logged_date=logged_date,
                       completed=completed, value=value)
        db.add(log)

    await db.commit()
    await db.refresh(log)

    # Fetch challenge_id for this commitment
    cm_result = await db.execute(
        select(HabitCommitment).where(HabitCommitment.id == commitment_id)
    )
    cm = cm_result.scalar_one()
    challenge_id = cm.challenge_id

    # Compute streak details after logging
    streak = await get_streak(db, challenge_id, user_id)

    # Real-time push notifications
    if completed:
        total_result = await db.execute(
            select(func.count()).where(HabitCommitment.challenge_id == challenge_id)
        )
        total_habits = total_result.scalar() or 0

        done_result = await db.execute(
            select(func.count())
            .select_from(DailyLog)
            .join(HabitCommitment, DailyLog.commitment_id == HabitCommitment.id)
            .where(
                HabitCommitment.challenge_id == challenge_id,
                DailyLog.logged_date == logged_date,
                DailyLog.completed == True,
            )
        )
        done_today = done_result.scalar() or 0

        if total_habits > 0 and done_today >= total_habits:
            await fire_habit_perfect_day(db, user_id, challenge_id)
        elif streak.get("current_streak", 0) in _STREAK_MILESTONES:
            await fire_habit_streak_milestone(db, user_id, streak["current_streak"])

    return {
        "id": log.id,
        "commitment_id": log.commitment_id,
        "logged_date": log.logged_date,
        "completed": log.completed,
        "value": log.value,
        "logged_at": log.logged_at,
        **streak,
    }


async def get_leaderboard(db: AsyncSession, days: int = 7) -> list[dict]:
    from sqlalchemy.orm import selectinload
    today = date.today()
    period_start = today - timedelta(days=days - 1)
    prev_start   = period_start - timedelta(days=days)
    prev_end     = period_start - timedelta(days=1)

    result = await db.execute(
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.user),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.logs),
        )
        .where(HabitChallenge.status == ChallengeStatus.active)
    )
    challenges = result.scalars().all()

    def compute_stats(challenge, start: date, end: date) -> dict:
        total_habits = len(challenge.commitments)
        actual_start = max(start, challenge.started_at)
        actual_end   = min(end, today)
        if not total_habits or actual_start > actual_end:
            return {"completion_pct": 0.0, "completed": 0, "possible": 0, "streak": 0}
        days_count = (actual_end - actual_start).days + 1
        possible   = total_habits * days_count
        completed  = sum(
            1 for cm in challenge.commitments
            for log in cm.logs
            if actual_start <= log.logged_date <= actual_end and log.completed
        )
        # streak is only computed for current window
        by_date: dict[date, int] = defaultdict(int)
        for cm in challenge.commitments:
            for log in cm.logs:
                if log.completed:
                    by_date[log.logged_date] += 1
        streak = 0
        d = today
        min_required = max(1, int(total_habits * 0.5 + 0.0001))
        while d >= challenge.started_at:
            if by_date.get(d, 0) >= min_required:
                streak += 1
                d -= timedelta(days=1)
            else:
                break
        return {
            "completion_pct": round(completed / max(possible, 1) * 100, 1),
            "completed": completed,
            "possible": possible,
            "streak": streak,
        }

    entries = []
    for challenge in challenges:
        user = challenge.user
        if not user:
            continue
        curr = compute_stats(challenge, period_start, today)
        prev = compute_stats(challenge, prev_start, prev_end)
        entries.append({
            "user_id":         str(user.id),
            "name":            user.name or user.email,
            "profile_pic_url": user.profile_pic_url,
            "challenge_id":    challenge.id,
            "_prev_pct":       prev["completion_pct"],
            **curr,
        })

    # Sort current window — best completion%, then streak as tiebreaker
    entries.sort(key=lambda x: (-x["completion_pct"], -x["streak"]))
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    # Derive previous ranks from previous period stats
    prev_sorted = sorted(entries, key=lambda x: -x["_prev_pct"])
    prev_rank_map = {e["user_id"]: i + 1 for i, e in enumerate(prev_sorted)}

    for e in entries:
        prev_rank      = prev_rank_map.get(e["user_id"], e["rank"])
        e["rank_change"] = prev_rank - e["rank"]   # positive = moved UP
        del e["_prev_pct"]

    return entries


async def get_challenge_history(db: AsyncSession, user_id: str) -> list[dict]:
    result = await db.execute(
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.habit),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.logs),
        )
        .where(HabitChallenge.user_id == user_id)
        .order_by(HabitChallenge.started_at.desc())
    )
    challenges = result.scalars().all()

    today = date.today()
    history = []

    for challenge in challenges:
        total_habits = len(challenge.commitments)
        total_days = (challenge.ends_at - challenge.started_at).days + 1
        days_elapsed = min((today - challenge.started_at).days + 1, total_days)

        by_date: dict[date, int] = defaultdict(int)
        habit_entries = []
        # log_map[commitment_id][logged_date] = log
        log_map: dict[int, dict[date, object]] = defaultdict(dict)
        sorted_commitments = sorted(challenge.commitments, key=lambda x: x.sort_order)

        for commitment in sorted_commitments:
            completed_days = sum(1 for log in commitment.logs if log.completed)
            habit_entries.append({
                "commitment_id": commitment.id,
                "habit": commitment.habit,
                "days_completed": completed_days,
                "days_total": days_elapsed,
                "completion_pct": round(completed_days / max(days_elapsed, 1) * 100, 1),
            })
            for log in commitment.logs:
                log_map[commitment.id][log.logged_date] = log
                if log.completed:
                    by_date[log.logged_date] += 1

        perfect_days = sum(1 for v in by_date.values() if v >= total_habits) if total_habits else 0

        # SHIELD LOGIC
        # 1 shield is earned for every 4-day streak (can earn multiple)
        # If streak breaks and shield is available, shield is consumed and streak continues
        # Only one shield can be used per break
        # Calculate shields for the whole challenge period
        streaks = []  # list of (start, end, length)
        shields_earned = 0
        shields_used = 0
        effective_streak = 0
        min_required = max(1, int(total_habits * 0.5 + 0.0001))
        d = challenge.started_at
        end = min(today, challenge.ends_at)
        cur = 0
        while d <= end:
            if by_date.get(d, 0) >= min_required:
                cur += 1
            else:
                if cur > 0:
                    streaks.append(cur)
                cur = 0
            d += timedelta(days=1)
        if cur > 0:
            streaks.append(cur)

        # Calculate shields earned
        shields_earned = sum(s // 4 for s in streaks)

        # Now, simulate shield protection for the current streak (active only)
        # If today hasn't been logged yet, don't penalise — start from yesterday
        current = 0
        shields_left = shields_earned
        shield_used_on_dates = []
        if challenge.status == ChallengeStatus.active:
            streak_start = today if by_date.get(today, 0) >= min_required else today - timedelta(days=1)
            d = streak_start
            while d >= challenge.started_at:
                if by_date.get(d, 0) >= min_required:
                    current += 1
                    d -= timedelta(days=1)
                else:
                    if shields_left > 0:
                        shields_used += 1
                        shields_left -= 1
                        shield_used_on_dates.append(d)
                        current += 1
                        d -= timedelta(days=1)
                    else:
                        break
        effective_streak = current

        # longest streak across entire challenge (no shield protection)
        longest = max(streaks) if streaks else 0

        # Build per-day breakdown
        daily_logs = []
        d = challenge.started_at
        end_day = min(today, challenge.ends_at)
        while d <= end_day:
            day_habits = []
            completed_count = 0
            for cm in sorted_commitments:
                log = log_map[cm.id].get(d)
                completed = log.completed if log else False
                if completed:
                    completed_count += 1
                day_habits.append({
                    "commitment_id": cm.id,
                    "habit_slug":    cm.habit.slug,
                    "habit_label":   cm.habit.label,
                    "completed":     completed,
                    "value":         log.value if log else None,
                })
            daily_logs.append({
                "date":            d,
                "day_number":      (d - challenge.started_at).days + 1,
                "habits":          day_habits,
                "all_completed":   completed_count == total_habits,
                "completed_count": completed_count,
                "total_count":     total_habits,
            })
            d += timedelta(days=1)

        history.append({
            "id": challenge.id,
            "pack_id": challenge.pack_id,
            "status": challenge.status,
            "started_at": challenge.started_at,
            "ends_at": challenge.ends_at,
            "total_days": total_days,
            "days_elapsed": days_elapsed,
            "perfect_days": perfect_days,
            "completion_pct": round(perfect_days / max(days_elapsed, 1) * 100, 1),
            "current_streak": current,
            "longest_streak": longest,
            "shields_earned": shields_earned,
            "shields_used": shields_used,
            "effective_streak": effective_streak,
            "shield_used_on_dates": shield_used_on_dates,
            "habits": habit_entries,
            "daily_logs": daily_logs,
        })

    return history


async def get_streak(db: AsyncSession, challenge_id: int, user_id: str) -> dict:
    result = await db.execute(
        select(HabitChallenge).where(
            HabitChallenge.id == challenge_id,
            HabitChallenge.user_id == user_id,
        )
    )
    challenge = result.scalar_one_or_none()
    if not challenge:
        raise HTTPException(404, "Challenge not found")

    commitments_result = await db.execute(
        select(HabitCommitment).where(HabitCommitment.challenge_id == challenge_id)
    )
    commitments = commitments_result.scalars().all()
    total = len(commitments)
    if not total:
        return {"challenge_id": challenge_id, "current_streak": 0,
                "longest_streak": 0, "perfect_days": 0, "completion_pct": 0.0}

    logs_result = await db.execute(
        select(DailyLog).where(
            DailyLog.commitment_id.in_([c.id for c in commitments]),
            DailyLog.completed == True,
        )
    )
    logs = logs_result.scalars().all()

    by_date: dict[date, int] = defaultdict(int)
    for log in logs:
        by_date[log.logged_date] += 1

    today = date.today()
    days_elapsed = (today - challenge.started_at).days + 1
    perfect_days = sum(1 for v in by_date.values() if v >= total)

    # SHIELD LOGIC (same as in history)
    streaks = []
    shields_earned = 0
    shields_used = 0
    effective_streak = 0
    min_required = max(1, int(total * 0.5 + 0.0001))
    d = challenge.started_at
    cur = 0
    while d <= today:
        if by_date.get(d, 0) >= min_required:
            cur += 1
        else:
            if cur > 0:
                streaks.append(cur)
            cur = 0
        d += timedelta(days=1)
    if cur > 0:
        streaks.append(cur)

    shields_earned = sum(s // 4 for s in streaks)

    # Simulate shield protection for current streak
    # If today hasn't been logged yet, don't penalise — start from yesterday
    streak_start = today if by_date.get(today, 0) >= min_required else today - timedelta(days=1)
    current = 0
    shields_left = shields_earned
    shield_used_on_dates = []
    d = streak_start
    while d >= challenge.started_at:
        if by_date.get(d, 0) >= min_required:
            current += 1
            d -= timedelta(days=1)
        else:
            if shields_left > 0:
                shields_used += 1
                shields_left -= 1
                shield_used_on_dates.append(d)
                current += 1
                d -= timedelta(days=1)
            else:
                break
    effective_streak = current

    # longest streak (no shield protection)
    longest = max(streaks) if streaks else 0

    return {
        "challenge_id": challenge_id,
        "current_streak": current,
        "longest_streak": longest,
        "perfect_days": perfect_days,
        "completion_pct": round(perfect_days / max(days_elapsed, 1) * 100, 1),
        "shields_earned": shields_earned,
        "shields_used": shields_used,
        "effective_streak": effective_streak,
        "shield_used_on_dates": shield_used_on_dates,
    }
