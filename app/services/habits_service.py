from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException

from app.models import Challenge, ChallengeStatus, DailyLog, Habit, HabitCommitment

 

def _get_habit(db: Session, slug: str) -> Habit:
    h = db.query(Habit).filter(Habit.slug == slug).first()
    if not h:
        raise HTTPException(404, f"Habit '{slug}' not found")
    return h


def create_challenge(db: Session, user_id: int, body: ChallengeCreate) -> Challenge:
    # abandon any existing active challenge
    db.query(Challenge).filter(
        Challenge.user_id == user_id,
        Challenge.status == ChallengeStatus.active
    ).update({"status": ChallengeStatus.abandoned})

    today = date.today()
    challenge = Challenge(
        user_id=user_id,
        pack_id=body.pack_id,
        started_at=today,
        ends_at=today + timedelta(days=20),
    )
    db.add(challenge)
    db.flush()

    for order, slug in enumerate(body.habit_slugs):
        habit = _get_habit(db, slug)
        db.add(HabitCommitment(challenge_id=challenge.id, habit_id=habit.id, sort_order=order))

    db.commit()
    db.refresh(challenge)
    return challenge


def get_active_challenge(db: Session, user_id: int) -> Challenge:
    c = (
        db.query(Challenge)
        .options(
            joinedload(Challenge.commitments).joinedload(HabitCommitment.habit),
            joinedload(Challenge.commitments).joinedload(HabitCommitment.logs),
        )
        .filter(Challenge.user_id == user_id, Challenge.status == ChallengeStatus.active)
        .first()
    )
    if not c:
        raise HTTPException(404, "No active challenge")
    return c


def get_today(db: Session, user_id: int, target_date: date | None = None) -> dict:
    challenge = get_active_challenge(db, user_id)
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


def upsert_log(db: Session, user_id: int, commitment_id: int,
               logged_date: date, completed: bool, value: int | None) -> DailyLog:
    commitment = (
        db.query(HabitCommitment).join(Challenge)
        .filter(HabitCommitment.id == commitment_id, Challenge.user_id == user_id)
        .first()
    )
    if not commitment:
        raise HTTPException(404, "Commitment not found")

    log = db.query(DailyLog).filter(
        DailyLog.commitment_id == commitment_id,
        DailyLog.logged_date == logged_date,
    ).first()

    if log:
        log.completed = completed
        log.value = value
    else:
        log = DailyLog(commitment_id=commitment_id, logged_date=logged_date,
                       completed=completed, value=value)
        db.add(log)

    db.commit()
    db.refresh(log)
    return log


def get_streak(db: Session, challenge_id: int, user_id: int) -> dict:
    challenge = db.query(Challenge).filter(
        Challenge.id == challenge_id, Challenge.user_id == user_id
    ).first()
    if not challenge:
        raise HTTPException(404, "Challenge not found")

    commitments = db.query(HabitCommitment).filter(
        HabitCommitment.challenge_id == challenge_id
    ).all()
    total = len(commitments)
    if not total:
        return {"challenge_id": challenge_id, "current_streak": 0,
                "longest_streak": 0, "perfect_days": 0, "completion_pct": 0.0}

    logs = db.query(DailyLog).filter(
        DailyLog.commitment_id.in_([c.id for c in commitments]),
        DailyLog.completed == True,
    ).all()

    by_date: dict[date, int] = defaultdict(int)
    for log in logs:
        by_date[log.logged_date] += 1

    today = date.today()
    days_elapsed = (today - challenge.started_at).days + 1
    perfect_days = sum(1 for v in by_date.values() if v >= total)

    # current streak
    current = 0
    d = today
    while d >= challenge.started_at:
        if by_date.get(d, 0) >= total:
            current += 1
            d -= timedelta(days=1)
        else:
            break

    # longest streak
    longest = cur = 0
    d = challenge.started_at
    while d <= today:
        if by_date.get(d, 0) >= total:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
        d += timedelta(days=1)

    return {
        "challenge_id": challenge_id,
        "current_streak": current,
        "longest_streak": longest,
        "perfect_days": perfect_days,
        "completion_pct": round(perfect_days / max(days_elapsed, 1) * 100, 1),
    }