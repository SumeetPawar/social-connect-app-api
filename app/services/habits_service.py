from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models import HabitChallenge, ChallengeStatus, DailyLog, Habit, HabitCommitment, UserHabit, User
from app.schemas.habits import ChallengeCreate
from app.services.reminder_service import fire_habit_perfect_day, fire_habit_streak_milestone

_STREAK_MILESTONES = {3, 7, 14, 21, 30}


# ── Shield / streak helper ────────────────────────────────────────────────────

def _compute_shield_streak(
    by_date: dict,
    started_at: date,
    end_day: date,
    total_habits: int,
    today: date,
) -> dict:
    """
    Shared shield-and-streak logic used by get_streak() and get_history().

    Rules:
    - A day "counts" if completed habits >= min_required (50% floor, min 1).
    - Shields are earned 1 at a time: every 4 consecutive good days earn 1 shield.
      Max 1 shield held at a time — must use it before earning the next.
    - A shield bridges 1 missed day, keeping the effective streak alive.
      After using a shield, the 4-day counter resets.
    - current_streak  = raw consecutive good days (no shield help) from today back.
    - effective_streak = streak computed by the forward simulation (shields applied).
    - Today grace: if today has zero logs at all, skip without penalty (day not over).
    """
    min_required = max(1, -(-total_habits // 2))  # ceil(total/2)


    # ── Forward simulation: effective streak and shield-protected longest streak ──
    shield_bank = 0       # 0 or 1
    consecutive = 0       # good days since last shield earned or last gap
    effective = 0         # current shield-protected streak (up to today)
    max_effective = 0     # longest shield-protected streak
    shields_earned = 0
    shields_used = 0
    shield_used_on_dates: list[date] = []
    current_segment = 0   # for current_streak (raw, no shields)
    raw_current = 0

    d = started_at
    today_grace = False
    while d <= end_day:
        good = by_date.get(d, 0) >= min_required
        today_grace = (d == today and not good)  # grace all day until min_required is met

        if good:
            effective += 1
            consecutive += 1
            current_segment += 1
            if consecutive == 4 and shield_bank == 0:
                shield_bank = 1
                shields_earned += 1
                consecutive = 0   # reset — next shield needs another 4 days
        elif today_grace:
            # Don't break streak, don't increment
            pass
        else:
            # Missed day
            if shield_bank > 0:
                shield_bank -= 1
                shields_used += 1
                shield_used_on_dates.append(d)
                effective += 1
                consecutive = 0   # reset after using shield
                # current_segment does NOT increment (raw streak broken)
            else:
                # Streak broken (both effective and raw)
                if effective > max_effective:
                    max_effective = effective
                effective = 0
                consecutive = 0
                shield_bank = 0
                current_segment = 0
        d += timedelta(days=1)

    # After loop, check if current streaks are the longest
    if effective > max_effective:
        max_effective = effective

    # Raw current streak: if last segment touches today or yesterday
    # (raw streak = consecutive good days, no shields)
    if current_segment > 0:
        if end_day == today or end_day == today - timedelta(days=1):
            raw_current = current_segment

    return {
        "current_streak":       raw_current,   # raw consecutive good days (no shields)
        "effective_streak":     effective,     # shield-protected streak up to today
        "longest_streak":       max_effective, # shield-protected longest streak
        "shields_earned":       shields_earned,
        "shields_used":         shields_used,
        "shield_used_on_dates": shield_used_on_dates,
    }


async def _get_habit(db: AsyncSession, slug: str) -> Habit:
    result = await db.execute(select(Habit).where(Habit.slug == slug))
    h = result.scalar_one_or_none()
    if not h:
        raise HTTPException(404, f"Habit '{slug}' not found")
    return h


async def create_challenge(db: AsyncSession, user_id: str, body: ChallengeCreate) -> HabitChallenge:
    # Validate custom habit IDs belong to this user
    for uid in body.custom_habit_ids:
        uh = (await db.execute(
            select(UserHabit).where(UserHabit.id == uid, UserHabit.user_id == user_id)
        )).scalar_one_or_none()
        if not uh:
            raise HTTPException(404, f"Custom habit {uid} not found")

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

    order = 0
    for slug in body.habit_slugs:
        habit = await _get_habit(db, slug)
        db.add(HabitCommitment(challenge_id=challenge.id, habit_id=habit.id, sort_order=order))
        order += 1

    for uid in body.custom_habit_ids:
        db.add(HabitCommitment(challenge_id=challenge.id, user_habit_id=uid, sort_order=order))
        order += 1

    await db.commit()
    await db.refresh(challenge)
    return challenge


async def get_active_challenge(db: AsyncSession, user_id: str) -> HabitChallenge:
    result = await db.execute(
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.habit),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.user_habit),
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
        if c.user_habit:
            habit_data = {
                "commitment_id": c.id,
                "is_custom": True,
                "user_habit_id": c.user_habit_id,
                "name": c.user_habit.name,
                "emoji": c.user_habit.emoji,
                "has_counter": False,
            }
        else:
            h = c.habit
            habit_data = {
                "commitment_id": c.id,
                "is_custom": False,
                "habit_id": c.habit_id,
                "name": h.label,
                "slug": h.slug,
                "description": h.description,
                "why": h.why,
                "impact": h.impact,
                "category": h.category,
                "tier": h.tier,
                "has_counter": h.has_counter,
                "unit": h.unit,
                "target": h.target,
            }
        habits_today.append({
            "commitment_id": c.id,
            "habit": habit_data,
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
        elif streak.get("effective_streak", 0) in _STREAK_MILESTONES:
            await fire_habit_streak_milestone(db, user_id, streak["effective_streak"])

    return {
        "id": log.id,
        "commitment_id": log.commitment_id,
        "logged_date": log.logged_date,
        "completed": log.completed,
        "value": log.value,
        "logged_at": log.logged_at,
        **streak,
    }


async def get_leaderboard(
    db: AsyncSession,
    days: int = 7,
    department_id: str | None = None,
    current_user_id: str | None = None,
) -> list[dict]:
    from sqlalchemy.orm import selectinload
    today = date.today()
    period_start = today - timedelta(days=days - 1)
    prev_start   = period_start - timedelta(days=days)
    prev_end     = period_start - timedelta(days=1)

    q = (
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.user),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.logs),
        )
        .where(HabitChallenge.status == ChallengeStatus.active)
    )
    if department_id:
        q = q.join(User, User.id == HabitChallenge.user_id).where(
            User.department_id == department_id
        )
    result = await db.execute(q)
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
        by_date: dict[date, int] = defaultdict(int)
        for cm in challenge.commitments:
            for log in cm.logs:
                if log.completed:
                    by_date[log.logged_date] += 1
        streak = 0
        d = today
        min_required = max(1, -(-total_habits // 2))  # ceil(total/2)
        while d >= challenge.started_at:
            if by_date.get(d, 0) >= min_required:
                streak += 1
                d -= timedelta(days=1)
            else:
                break
        return {
            "completion_pct": round(completed / max(possible, 1) * 100, 1),
            "completed":      completed,
            "possible":       possible,
            "streak":         streak,
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
            "is_me":           str(user.id) == current_user_id,
            "_prev_pct":       prev["completion_pct"],
            **curr,
        })

    # Sort: best completion %, then total habits completed as tiebreaker
    entries.sort(key=lambda x: (-x["completion_pct"], -x["completed"]))
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    # Rank change vs previous period
    prev_sorted  = sorted(entries, key=lambda x: (-x["_prev_pct"], -x["completed"]))
    prev_rank_map = {e["user_id"]: i + 1 for i, e in enumerate(prev_sorted)}
    for e in entries:
        prev_rank        = prev_rank_map.get(e["user_id"], e["rank"])
        e["rank_change"] = prev_rank - e["rank"]  # positive = moved UP
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

        # SHIELD LOGIC — delegated to shared helper
        end = min(today, challenge.ends_at)
        ss = _compute_shield_streak(by_date, challenge.started_at, end, total_habits, today)
        shields_earned       = ss["shields_earned"]
        shields_used         = ss["shields_used"]
        shield_used_on_dates = ss["shield_used_on_dates"]
        effective_streak     = ss["effective_streak"]
        current              = ss["current_streak"]    # raw streak (no shields)
        longest              = ss["longest_streak"]

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

    ss = _compute_shield_streak(by_date, challenge.started_at, today, total, today)

    return {
        "challenge_id":         challenge_id,
        "current_streak":       ss["current_streak"],
        "longest_streak":       ss["longest_streak"],
        "perfect_days":         perfect_days,
        "completion_pct":       round(perfect_days / max(days_elapsed, 1) * 100, 1),
        "shields_earned":       ss["shields_earned"],
        "shields_used":         ss["shields_used"],
        "effective_streak":     ss["effective_streak"],
        "shield_used_on_dates": ss["shield_used_on_dates"],
    }
