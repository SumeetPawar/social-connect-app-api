# habits.py — Habit library + Habit challenge endpoints
from typing import Optional
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import Habit, HabitChallenge, HabitCommitment, UserHabit, ChallengeStatus, User
from app.schemas.habits import (
    HabitOut, ChallengeCreate, ChallengeOut, LogCreate, LogOut, LogWithStreakOut,
    TodayOut, StreakOut, ChallengeHistoryOut, LeaderboardOut,
    CustomHabitCreate, CustomHabitOut, AnyHabitOut,
)
from app.services import habits_service as svc

# ── Habit library ─────────────────────────────────────────────────────────────
habits_router = APIRouter(prefix="/api/habits", tags=["habits"])


@habits_router.get("", response_model=list[HabitOut])
async def list_habits(
    category: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Habit)
    if category:
        q = q.where(Habit.category == category)
    if tier:
        q = q.where(Habit.tier == tier)
    result = await db.execute(q.order_by(Habit.id))
    return result.scalars().all()


@habits_router.get("/{slug}", response_model=HabitOut)
async def get_habit(slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Habit).where(Habit.slug == slug))
    h = result.scalar_one_or_none()
    if not h:
        raise HTTPException(404, f"Habit '{slug}' not found")
    return h


# ── Habit challenges ──────────────────────────────────────────────────────────
challenges_router = APIRouter(prefix="/api/habit-challenges", tags=["habit-challenges"])


@challenges_router.post("", response_model=ChallengeOut, status_code=201)
async def start_challenge(
    body: ChallengeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await svc.create_challenge(db, str(current_user.id), body)
    return await _out(c, db)


@challenges_router.get("/active", response_model=ChallengeOut)
async def active(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await svc.get_active_challenge(db, str(current_user.id))
    return await _out(c, db)


@challenges_router.get("/today", response_model=TodayOut)
async def today(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.get_today(db, str(current_user.id), target_date)


@challenges_router.get("/leaderboard", response_model=LeaderboardOut)
async def leaderboard(
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Habit leaderboard for the current user's department.
    Returns rank, completion %, completed/possible habit counts, streak and rank change.
    """
    today = date.today()
    entries = await svc.get_leaderboard(
        db,
        days=days,
        department_id=str(current_user.department_id),
        current_user_id=str(current_user.id),
    )
    return {
        "period_days":  days,
        "period_start": today - timedelta(days=days - 1),
        "period_end":   today,
        "entries":      entries,
    }


@challenges_router.get("/history", response_model=list[ChallengeHistoryOut])
async def challenge_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All challenge cycles for the current user — past and present — with per-habit and overall stats."""
    return await svc.get_challenge_history(db, str(current_user.id))


@challenges_router.post("/logs", response_model=LogWithStreakOut, status_code=201)
async def log_habit(
    body: LogCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.upsert_log(
        db, str(current_user.id), body.commitment_id,
        body.logged_date, body.completed, body.value,
    )


@challenges_router.get("/{challenge_id}/streak", response_model=StreakOut)
async def streak(
    challenge_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.get_streak(db, challenge_id, str(current_user.id))


@challenges_router.delete("/{challenge_id}", status_code=204)
async def abandon(
    challenge_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HabitChallenge).where(
            HabitChallenge.id == challenge_id,
            HabitChallenge.user_id == str(current_user.id),
        )
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Challenge not found")
    c.status = ChallengeStatus.abandoned
    await db.commit()


@challenges_router.delete("/{challenge_id}/hard", status_code=204)
async def hard_delete(
    challenge_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HabitChallenge).where(
            HabitChallenge.id == challenge_id,
            HabitChallenge.user_id == str(current_user.id),
        )
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Challenge not found")
    await db.delete(c)
    await db.commit()


async def _out(challenge: HabitChallenge, db: AsyncSession) -> dict:
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(HabitChallenge)
        .options(
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.habit),
            selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.user_habit),
        )
        .where(HabitChallenge.id == challenge.id)
    )
    c = result.scalar_one()
    habits = []
    for cm in sorted(c.commitments, key=lambda x: x.sort_order):
        if cm.user_habit:
            habits.append(AnyHabitOut(
                commitment_id=cm.id,
                is_custom=True,
                user_habit_id=cm.user_habit_id,
                name=cm.user_habit.name,
                emoji=cm.user_habit.emoji,
            ))
        else:
            h = cm.habit
            habits.append(AnyHabitOut(
                commitment_id=cm.id,
                is_custom=False,
                habit_id=cm.habit_id,
                name=h.label,
                slug=h.slug,
                description=h.description,
                why=h.why,
                impact=h.impact,
                category=str(h.category.value) if hasattr(h.category, "value") else str(h.category),
                tier=str(h.tier.value) if hasattr(h.tier, "value") else str(h.tier),
                has_counter=h.has_counter,
                unit=h.unit,
                target=h.target,
            ))
    return {
        "id": c.id,
        "pack_id": c.pack_id,
        "status": c.status,
        "started_at": c.started_at,
        "ends_at": c.ends_at,
        "habits": habits,
    }


# ── Custom habits ─────────────────────────────────────────────────────────────

@habits_router.get("/custom", response_model=list[CustomHabitOut])
async def list_custom_habits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all custom habits created by the current user."""
    result = await db.execute(
        select(UserHabit)
        .where(UserHabit.user_id == str(current_user.id))
        .order_by(UserHabit.created_at)
    )
    return result.scalars().all()


@habits_router.post("/custom", response_model=CustomHabitOut, status_code=201)
async def create_custom_habit(
    body: CustomHabitCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new custom habit.
    Name must be unique per user (case-sensitive).
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Habit name cannot be empty")
    if len(name) > 100:
        raise HTTPException(400, "Habit name must be 100 characters or fewer")

    existing = (await db.execute(
        select(UserHabit).where(
            UserHabit.user_id == str(current_user.id),
            func.lower(UserHabit.name) == name.lower(),
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "You already have a custom habit with this name")

    uh = UserHabit(user_id=str(current_user.id), name=name, emoji=body.emoji)
    db.add(uh)
    await db.commit()
    await db.refresh(uh)
    return uh


@habits_router.delete("/custom/{habit_id}", status_code=204)
async def delete_custom_habit(
    habit_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a custom habit.
    Blocked if the habit is part of the user's active challenge.
    """
    uh = (await db.execute(
        select(UserHabit).where(
            UserHabit.id == habit_id,
            UserHabit.user_id == str(current_user.id),
        )
    )).scalar_one_or_none()
    if not uh:
        raise HTTPException(404, "Custom habit not found")

    # Block deletion if used in an active challenge
    in_use = (await db.execute(
        select(func.count())
        .select_from(HabitCommitment)
        .join(HabitChallenge, HabitChallenge.id == HabitCommitment.challenge_id)
        .where(
            HabitCommitment.user_habit_id == habit_id,
            HabitChallenge.status == ChallengeStatus.active,
        )
    )).scalar()
    if in_use:
        raise HTTPException(409, "Cannot delete a habit that is part of your active challenge")

    await db.delete(uh)
    await db.commit()
