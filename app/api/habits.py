# habits.py — Habit library + Habit challenge endpoints
from typing import Optional
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import Habit, HabitChallenge, HabitCommitment, ChallengeStatus, User
from app.schemas.habits import (
    HabitOut, ChallengeCreate, ChallengeOut, LogCreate, LogOut, LogWithStreakOut,
    TodayOut, StreakOut, ChallengeHistoryOut, LeaderboardOut,
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
    Leaderboard of all users with active habit challenges.
    Returns completion %, habit counts, streak and rank change vs previous period.
    """
    today = date.today()
    entries = await svc.get_leaderboard(db, days)
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
        .options(selectinload(HabitChallenge.commitments).selectinload(HabitCommitment.habit))
        .where(HabitChallenge.id == challenge.id)
    )
    c = result.scalar_one()
    return {
        "id": c.id,
        "pack_id": c.pack_id,
        "status": c.status,
        "started_at": c.started_at,
        "ends_at": c.ends_at,
        "habits": [cm.habit for cm in sorted(c.commitments, key=lambda x: x.sort_order)],
    }
