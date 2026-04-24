"""
partners.py — Accountability partner management + peer habit nudge.

Endpoints:
  GET    /api/partners                    — list my partners
  POST   /api/partners/request            — send a partner request
  PATCH  /api/partners/{id}/respond       — accept | reject | block
  DELETE /api/partners/{id}               — remove partner
  POST   /api/partners/nudge              — send one accountability nudge (12-21 IST, 1/day)
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User, PushSubscription
from app.services.notification_service import write_inbox
from app.services.push_notify import send_web_push, PushResult

router = APIRouter(prefix="/api/partners", tags=["partners"])

_IST = ZoneInfo("Asia/Kolkata")
_NUDGE_START = 12   # 12:00 IST
_NUDGE_END   = 21   # 21:00 IST


# ── Request/response schemas ──────────────────────────────────────────────────

class PartnerRequestBody(BaseModel):
    user_id: str


class PartnerRespondBody(BaseModel):
    action: str   # accept | reject | block


class NudgeBody(BaseModel):
    receiver_user_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_approved_pair(db: AsyncSession, uid_a: str, uid_b: str):
    """Return the partner row if both users are approved partners, else None."""
    row = await db.execute(text("""
        SELECT id FROM accountability_partners
        WHERE  status = 'approved'
          AND  ((requester_id = :a AND partner_id = :b)
             OR (requester_id = :b AND partner_id = :a))
    """), {"a": uid_a, "b": uid_b})
    return row.scalar_one_or_none()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_partners(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all accountability partners (pending, approved, rejected — not blocked)."""
    rows = await db.execute(text("""
        SELECT ap.id,
               ap.status,
               ap.approved_at,
               ap.created_at,
               CASE WHEN ap.requester_id = :uid THEN ap.partner_id
                    ELSE ap.requester_id END  AS other_user_id,
               u.name                         AS other_name,
               CASE WHEN ap.requester_id = :uid THEN 'sent'
                    ELSE 'received' END        AS direction
        FROM   accountability_partners ap
        JOIN   users u ON u.id = CASE WHEN ap.requester_id = :uid THEN ap.partner_id
                                       ELSE ap.requester_id END
        WHERE  (ap.requester_id = :uid OR ap.partner_id = :uid)
          AND  ap.status != 'blocked'
        ORDER  BY ap.created_at DESC
    """), {"uid": str(user.id)})
    return {"partners": [dict(r) for r in rows.mappings()]}


@router.post("/request", status_code=201)
async def request_partner(
    body: PartnerRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send an accountability partner request to another user."""
    target_id = str(body.user_id)
    if target_id == str(user.id):
        raise HTTPException(400, "Cannot partner with yourself")

    # Verify target exists
    target_row = await db.execute(select(User).where(User.id == target_id))
    target_user = target_row.scalar_one_or_none()
    if not target_user:
        raise HTTPException(404, "User not found")

    # Check for existing relationship (either direction)
    existing = await db.execute(text("""
        SELECT id, status FROM accountability_partners
        WHERE  (requester_id = :a AND partner_id = :b)
            OR (requester_id = :b AND partner_id = :a)
    """), {"a": str(user.id), "b": target_id})
    row = existing.mappings().first()
    if row:
        if row["status"] == "blocked":
            raise HTTPException(403, "Cannot send request to this user")
        raise HTTPException(409, f"Relationship already exists (status: {row['status']})")

    await db.execute(text("""
        INSERT INTO accountability_partners (requester_id, partner_id, status)
        VALUES (:req, :par, 'pending')
    """), {"req": str(user.id), "par": target_id})

    # Notify target in inbox (no push — action-required card)
    requester_name = (user.name or "Someone").split()[0]
    await write_inbox(
        db,
        user_id=target_id,
        type="partner_request",
        template_key="partner_request_v1",
        payload={"requester_name": requester_name, "requester_id": str(user.id)},
        action_url="/socialapp/partners",
        actor_user_id=str(user.id),
        actor_name=requester_name,
    )
    await db.commit()
    return {"status": "ok", "message": "Partner request sent"}


@router.patch("/{partner_id}/respond")
async def respond_to_request(
    partner_id: int,
    body: PartnerRespondBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Accept, reject, or block a pending partner request sent to you."""
    if body.action not in ("accept", "reject", "block"):
        raise HTTPException(400, "action must be: accept | reject | block")

    record = await db.execute(text("""
        SELECT id, requester_id, partner_id, status
        FROM   accountability_partners
        WHERE  id = :pid
    """), {"pid": partner_id})
    rec = record.mappings().first()
    if not rec:
        raise HTTPException(404, "Partner request not found")
    if str(rec["partner_id"]) != str(user.id):
        raise HTTPException(403, "Not your request to respond to")
    if rec["status"] != "pending":
        raise HTTPException(409, f"Request already {rec['status']}")

    new_status = {"accept": "approved", "reject": "rejected", "block": "blocked"}[body.action]
    await db.execute(text("""
        UPDATE accountability_partners
        SET    status      = :status,
               approved_at = CASE WHEN :status = 'approved' THEN now() ELSE NULL END
        WHERE  id = :pid
    """), {"status": new_status, "pid": partner_id})

    # Notify requester on acceptance
    if body.action == "accept":
        responder_name = (user.name or "Someone").split()[0]
        await write_inbox(
            db,
            user_id=str(rec["requester_id"]),
            type="partner_accepted",
            template_key="partner_accepted_v1",
            payload={"partner_name": responder_name, "partner_id": str(user.id)},
            action_url="/socialapp/partners",
            actor_user_id=str(user.id),
            actor_name=responder_name,
        )

    await db.commit()
    return {"status": "ok", "result": new_status}


@router.delete("/{partner_id}")
async def remove_partner(
    partner_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove an accountability partner (either side can remove)."""
    result = await db.execute(text("""
        DELETE FROM accountability_partners
        WHERE  id = :pid
          AND  (requester_id = :uid OR partner_id = :uid)
        RETURNING id
    """), {"pid": partner_id, "uid": str(user.id)})
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Partner not found")
    await db.commit()
    return {"status": "ok"}


@router.post("/nudge")
async def send_partner_nudge(
    body: NudgeBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Send a single accountability nudge to an approved partner.

    Rules enforced:
      1. Must be approved partners
      2. IST time must be between 12:00 and 21:00
      3. Receiver must have at least one incomplete habit today
      4. Max 1 nudge received per user per day (DB unique constraint)

    This nudge bypasses the normal daily push cap — it has its own hard limit.
    """
    sender_id   = str(user.id)
    receiver_id = str(body.receiver_user_id)

    if receiver_id == sender_id:
        raise HTTPException(400, "Cannot nudge yourself")

    # 1. Approved partners only
    if not await _get_approved_pair(db, sender_id, receiver_id):
        raise HTTPException(403, "not_partners")

    # 2. Time window check (IST)
    now_ist   = datetime.now(_IST)
    local_day = now_ist.date()
    if not (_NUDGE_START <= now_ist.hour < _NUDGE_END):
        raise HTTPException(400, "outside_window — nudges allowed 12:00–21:00 IST only")

    # 3. Receiver must have incomplete habits today
    incomplete = await db.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM   habit_commitments hcm
        JOIN   habit_challenges  hc  ON hc.id = hcm.challenge_id
        WHERE  hc.user_id  = :receiver
          AND  hc.status   = 'active'
          AND  hc.ends_at >= :today
          AND  NOT EXISTS (
              SELECT 1
              FROM   daily_logs dl
              WHERE  dl.commitment_id = hcm.id
                AND  dl.logged_date   = :today
                AND  dl.completed     = true
          )
    """), {"receiver": receiver_id, "today": local_day})
    if (incomplete.scalar() or 0) == 0:
        raise HTTPException(400, "habits_already_complete")

    # 4. Insert nudge event — unique constraint rejects duplicates
    try:
        await db.execute(text("""
            INSERT INTO partner_nudge_events (sender_id, receiver_id, local_day)
            VALUES (:sender, :receiver, :day)
        """), {"sender": sender_id, "receiver": receiver_id, "day": local_day})
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "already_nudged_today")

    # 5. Build push text with sender name
    sender_name = (user.name or "Your partner").split()[0]
    push_title  = f"{sender_name} is cheering you on 🤝"
    push_body   = "You have habits to complete today. Finish them strong!"

    # 6. Write to notification inbox first (always saved even if push fails)
    await write_inbox(
        db,
        user_id=receiver_id,
        type="partner_nudge",
        template_key="partner_nudge_v1",
        payload={"sender_name": sender_name, "sender_id": sender_id},
        action_url="/socialapp/habits",
        push_title=push_title,
        push_body=push_body,
        actor_user_id=sender_id,
        actor_name=sender_name,
    )

    # 7. Fire push (outside daily cap — partner nudge has its own 1/day limit)
    subs_result = await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == receiver_id)
    )
    subs = subs_result.scalars().all()
    push_sent = 0
    for sub in subs:
        result, _ = send_web_push(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            {"title": push_title, "body": push_body, "url": "/socialapp/habits"},
        )
        if result == PushResult.OK:
            push_sent += 1
        elif result == PushResult.EXPIRED:
            await db.delete(sub)

    await db.commit()
    return {"status": "ok", "push_sent": push_sent}
