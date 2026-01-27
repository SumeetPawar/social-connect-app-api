from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.goals import SetDailyTargetRequest, SetDailyTargetResponse, CurrentGoalResponse
from app.models import ChallengeParticipant, Challenge, User
from app.auth.deps import get_current_user

router = APIRouter(prefix="/api/goals", tags=["goals"])  # ✅ Changed from /goals to /api/goals


@router.post("/set-target", response_model=SetDailyTargetResponse)
async def set_daily_target(
    payload: SetDailyTargetRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Set your personal daily step target for the current active challenge.
    Valid targets: 3000, 5000, 7500, or 10000 steps/day.
    """
    # Validate target
    if payload.daily_target not in [3000, 5000, 7500, 10000]:
        raise HTTPException(
            status_code=400,
            detail="Daily target must be one of: 3000, 5000, 7500, or 10000"
        )

    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    today = datetime.now(tz).date()

    # Find active challenge
    c_stmt = select(Challenge).where(
        Challenge.status == 'active',
        Challenge.start_date <= today,
        Challenge.end_date >= today,
    )
    c_res = await db.execute(c_stmt)
    challenge = c_res.scalar_one_or_none()

    if not challenge:
        raise HTTPException(
            status_code=404,
            detail="No active challenge available. Please wait for the next challenge."
        )

    # Check existing participation
    p_stmt = select(ChallengeParticipant).where(
        ChallengeParticipant.challenge_id == challenge.id,
        ChallengeParticipant.user_id == user.id,
        ChallengeParticipant.left_at.is_(None),
    )
    p_res = await db.execute(p_stmt)
    participant = p_res.scalar_one_or_none()

    if participant:
        # Already participating - check if locked
        if participant.selected_daily_target is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Target is locked. You cannot change it during an active challenge.",
            )
        # Set target
        participant.selected_daily_target = payload.daily_target
    else:
        # Join challenge with target
        participant = ChallengeParticipant(
            challenge_id=str(challenge.id),
            user_id=str(user.id),
            selected_daily_target=payload.daily_target,
        )
        db.add(participant)

    await db.commit()
    await db.refresh(participant)

    # Calculate weekly_target based on join date (today)
    join_date = datetime.now(ZoneInfo(user.timezone or "Asia/Kolkata")).date()
    days_left = (challenge.end_date - join_date).days + 1
    days_left = max(days_left, 0)  # Prevent negative if joined after end
    weekly_target = participant.selected_daily_target * days_left if participant.selected_daily_target else 0
    return {
        "challenge_id": str(challenge.id),
        "challenge_title": challenge.title,
        "daily_target": participant.selected_daily_target,
        "weekly_target": weekly_target,
        "challenge_start": challenge.start_date,
        "challenge_end": challenge.end_date,
    }


@router.get("/current", response_model=CurrentGoalResponse)
async def get_current_goal(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get your current daily target and challenge info.
    """
    tz = ZoneInfo(user.timezone or "Asia/Kolkata")
    today = datetime.now(tz).date()

    # Get active challenge participation
    stmt = select(Challenge, ChallengeParticipant).join(
        ChallengeParticipant,
        ChallengeParticipant.challenge_id == Challenge.id
    ).where(
        ChallengeParticipant.user_id == user.id,
        ChallengeParticipant.left_at.is_(None),
        Challenge.status == 'active',
        Challenge.start_date <= today,
        Challenge.end_date >= today,
    )
    res = await db.execute(stmt)
    result = res.first()

    if not result:
        raise HTTPException(
            status_code=404,
            detail="You haven't joined any active challenge."
        )

    challenge, participant = result

    if participant.selected_daily_target is None:
        raise HTTPException(
            status_code=404,
            detail="You've joined the challenge but haven't set a daily target yet."
        )

    # Calculate weekly_target based on join date (participant.joined_at)
    join_date = participant.joined_at.date() if hasattr(participant.joined_at, 'date') else participant.joined_at
    days_left = (challenge.end_date - join_date).days + 1
    days_left = max(days_left, 0)
    weekly_target = participant.selected_daily_target * days_left if participant.selected_daily_target else 0
    return {
        "challenge_id": str(challenge.id),
        "challenge_title": challenge.title,
        "daily_target": participant.selected_daily_target,
        "weekly_target": weekly_target,
        "challenge_start": challenge.start_date,
        "challenge_end": challenge.end_date,
        "has_target_set": True,
    }