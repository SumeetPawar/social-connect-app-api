# habits.py — drop in your endpoints folder
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Habit
from app.schemas.habits import HabitOut 

router = APIRouter(prefix="/habits", tags=["habits"])


@router.get("", response_model=list[HabitOut])
def list_habits(
    category: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Habit)
    if category:
        q = q.filter(Habit.category == category)
    if tier:
        q = q.filter(Habit.tier == tier)
    return q.order_by(Habit.id).all()


@router.get("/{slug}", response_model=HabitOut)
def get_habit(slug: str, db: Session = Depends(get_db)):
    h = db.query(Habit).filter(Habit.slug == slug).first()
    if not h:
        raise HTTPException(404, f"Habit '{slug}' not found")
    return h


 
 
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.core.security import get_current_user_id
from app.models import HabitCommitment, Challenge, ChallengeStatus
from app.schemas.habits import (
    ChallengeCreate, ChallengeOut, LogCreate, LogOut, TodayOut, StreakOut
)
from app.services import challenge_service as svc

router = APIRouter(prefix="/challenges", tags=["challenges"])


@router.post("", response_model=ChallengeOut, status_code=201)
def start_challenge(body: ChallengeCreate,
                    user_id: int = Depends(get_current_user_id),
                    db: Session = Depends(get_db)):
    c = svc.create_challenge(db, user_id, body)
    return _out(c, db)


@router.get("/active", response_model=ChallengeOut)
def active(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    return _out(svc.get_active_challenge(db, user_id), db)


@router.get("/today", response_model=TodayOut)
def today(target_date: Optional[date] = Query(None),
          user_id: int = Depends(get_current_user_id),
          db: Session = Depends(get_db)):
    return svc.get_today(db, user_id, target_date)


@router.post("/logs", response_model=LogOut, status_code=201)
def log_habit(body: LogCreate,
              user_id: int = Depends(get_current_user_id),
              db: Session = Depends(get_db)):
    return svc.upsert_log(db, user_id, body.commitment_id, body.logged_date, body.completed, body.value)


@router.get("/{challenge_id}/streak", response_model=StreakOut)
def streak(challenge_id: int,
           user_id: int = Depends(get_current_user_id),
           db: Session = Depends(get_db)):
    return svc.get_streak(db, challenge_id, user_id)


@router.delete("/{challenge_id}", status_code=204)
def abandon(challenge_id: int,
            user_id: int = Depends(get_current_user_id),
            db: Session = Depends(get_db)):
    c = db.query(Challenge).filter(Challenge.id == challenge_id, Challenge.user_id == user_id).first()
    if not c:
        raise HTTPException(404, "Challenge not found")
    c.status = ChallengeStatus.abandoned
    db.commit()


def _out(challenge, db):
    commitments = (
        db.query(HabitCommitment)
        .options(joinedload(HabitCommitment.habit))
        .filter(HabitCommitment.challenge_id == challenge.id)
        .order_by(HabitCommitment.sort_order)
        .all()
    )
    return {
        "id": challenge.id,
        "pack_id": challenge.pack_id,
        "status": challenge.status,
        "started_at": challenge.started_at,
        "ends_at": challenge.ends_at,
        "habits": [c.habit for c in commitments],
    }