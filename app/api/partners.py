"""
partners.py — Accountability partner management + peer habit nudge + live chat.

Endpoints:
  GET    /api/partners                        — list my partners
  POST   /api/partners/request                — send a partner request (same dept)
  POST   /api/partners/find-random            — self-service: match immediately or enter queue (202)
  DELETE /api/partners/queue                  — cancel waiting in queue
  PATCH  /api/partners/{id}/respond           — accept | reject | block
  PATCH  /api/partners/{id}/keep-vote         — vote to keep or change weekly partner
  DELETE /api/partners/{id}                   — remove partner
  POST   /api/partners/nudge                  — send one accountability nudge (12-21 IST, 1/day)
  GET    /api/partners/{id}/messages          — fetch chat history for a pair
  POST   /api/partners/{id}/messages          — send a message (WS delivery if online, else push)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User, PushSubscription
from app.services.notification_service import write_inbox
from app.services.push_notify import send_web_push, PushResult
from app.api.ws import is_online, notify_user

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


class KeepVoteBody(BaseModel):
    keep: bool


class SendMessageBody(BaseModel):
    body: str


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_approved_pair(db: AsyncSession, uid_a: str, uid_b: str):
    """Return the partner row id if both users are approved partners, else None."""
    row = await db.execute(text("""
        SELECT id FROM accountability_partners
        WHERE  status = 'approved'
          AND  ((requester_id = :a AND partner_id = :b)
             OR (requester_id = :b AND partner_id = :a))
    """), {"a": uid_a, "b": uid_b})
    return row.scalar_one_or_none()


async def _push_partner_message(db: AsyncSession, receiver_id: str, sender_name: str, msg_body: str):
    """Send a push notification for a new chat message."""
    subs_result = await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == receiver_id)
    )
    subs = subs_result.scalars().all()
    for sub in subs:
        result, _ = send_web_push(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            {
                "title": f"{sender_name}",
                "body": msg_body[:100],
                "url": "/socialapp/partners/chat",
            },
        )
        if result == PushResult.EXPIRED:
            await db.delete(sub)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_partners(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Partner screen — single call returning everything needed for the UI:
      - Relationship metadata (status, week_start, keep votes)
      - Partner profile (name, pic)
      - Partner's steps today + step streak
      - Partner's habits today (done / total / pct)
      - Unread message count
      - Nudge state (already nudged, can nudge)
    """
    uid = str(user.id)
    today = datetime.now(_IST).date()

    pairs = (await db.execute(text("""
        SELECT
            ap.id,
            ap.status,
            ap.assignment_type,
            ap.week_start,
            ap.approved_at,
            ap.keep_deadline,
            ap.requester_keep,
            ap.partner_keep,
            CASE WHEN ap.requester_id = :uid THEN ap.partner_id
                 ELSE ap.requester_id END  AS partner_id,
            CASE WHEN ap.requester_id = :uid THEN 'sent'
                 ELSE 'received' END       AS direction,
            pu.name                        AS partner_name,
            pu.profile_pic_url             AS partner_pic
        FROM accountability_partners ap
        JOIN users pu ON pu.id = CASE WHEN ap.requester_id = :uid
                                       THEN ap.partner_id
                                       ELSE ap.requester_id END
        WHERE (ap.requester_id = :uid OR ap.partner_id = :uid)
          AND ap.status IN ('approved', 'pending')
        ORDER BY ap.approved_at DESC NULLS LAST
    """), {"uid": uid})).mappings().all()

    if not pairs:
        return {
            "partners":        [],
            "seeking_partner": user.seeking_partner,
            "partner_opt_out": user.partner_opt_out,
        }

    partner_ids = [str(p["partner_id"]) for p in pairs]

    steps_rows = (await db.execute(text("""
        SELECT user_id, steps
        FROM daily_steps
        WHERE user_id = ANY(CAST(:ids AS uuid[])) AND day = :today
    """), {"ids": partner_ids, "today": today})).mappings().all()
    steps_map = {str(r["user_id"]): r["steps"] for r in steps_rows}

    streak_rows = (await db.execute(text("""
        SELECT user_id, COUNT(*) AS streak
        FROM (
            SELECT user_id, day,
                   day - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY day)::int AS grp
            FROM daily_steps
            WHERE user_id = ANY(CAST(:ids AS uuid[])) AND steps > 0 AND day >= current_date - 60
        ) g
        WHERE grp = (
            SELECT day - ROW_NUMBER() OVER (ORDER BY day)::int AS grp
            FROM daily_steps
            WHERE user_id = g.user_id AND steps > 0 AND day >= current_date - 60
            ORDER BY day DESC LIMIT 1
        )
        GROUP BY user_id
    """), {"ids": partner_ids})).mappings().all()
    streak_map = {str(r["user_id"]): r["streak"] for r in streak_rows}

    habit_rows = (await db.execute(text("""
        SELECT
            hc.user_id,
            COUNT(hcm.id)                                    AS total_habits,
            COUNT(dl.id) FILTER (WHERE dl.completed = true)  AS done_habits
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        LEFT JOIN daily_logs dl
            ON dl.commitment_id = hcm.id AND dl.logged_date = :today
        WHERE hc.user_id = ANY(CAST(:ids AS uuid[]))
          AND hc.status = 'active' AND hc.ends_at >= :today
        GROUP BY hc.user_id
    """), {"ids": partner_ids, "today": today})).mappings().all()
    habit_map = {str(r["user_id"]): r for r in habit_rows}

    # Habit streak: consecutive days where ALL active habits were completed
    habit_streak_rows = (await db.execute(text("""
        WITH daily_completion AS (
            SELECT
                hc.user_id,
                dl.logged_date,
                COUNT(hcm.id)                                   AS total,
                COUNT(dl.id) FILTER (WHERE dl.completed = true) AS done
            FROM habit_challenges hc
            JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
            LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
            WHERE hc.user_id = ANY(CAST(:ids AS uuid[]))
              AND hc.status = 'active'
              AND dl.logged_date >= current_date - 60
            GROUP BY hc.user_id, dl.logged_date
        ),
        perfect_days AS (
            SELECT user_id, logged_date
            FROM daily_completion
            WHERE total > 0 AND done = total
        ),
        streaks AS (
            SELECT user_id, logged_date,
                   logged_date - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY logged_date)::int AS grp
            FROM perfect_days
        )
        SELECT user_id, COUNT(*) AS habit_streak
        FROM streaks
        WHERE grp = (
            SELECT logged_date - ROW_NUMBER() OVER (ORDER BY logged_date)::int
            FROM perfect_days pd2
            WHERE pd2.user_id = streaks.user_id
            ORDER BY logged_date DESC LIMIT 1
        )
        GROUP BY user_id
    """), {"ids": partner_ids})).mappings().all()
    habit_streak_map = {str(r["user_id"]): r["habit_streak"] for r in habit_streak_rows}

    unread_rows = (await db.execute(text("""
        SELECT pair_id, COUNT(*) AS unread
        FROM partner_messages
        WHERE receiver_id = :uid AND read_at IS NULL
          AND pair_id = ANY(CAST(:pair_ids AS bigint[]))
        GROUP BY pair_id
    """), {"uid": uid, "pair_ids": [p["id"] for p in pairs]})).mappings().all()
    unread_map = {r["pair_id"]: r["unread"] for r in unread_rows}

    # Habit challenge details for partners (pack_id, started_at, ends_at)
    habit_challenge_rows = (await db.execute(text("""
        SELECT user_id, id AS challenge_id, pack_id, started_at, ends_at,
               (ends_at - current_date) AS days_remaining
        FROM habit_challenges
        WHERE user_id = ANY(CAST(:ids AS uuid[]))
          AND status = 'active' AND ends_at >= :today
    """), {"ids": partner_ids, "today": today})).mappings().all()
    habit_challenge_map = {str(r["user_id"]): r for r in habit_challenge_rows}

    already_nudged: set = set()  # no per-sender daily limit

    now_ist = datetime.now(_IST)
    nudge_window_open = _NUDGE_START <= now_ist.hour < _NUDGE_END

    result = []
    for p in pairs:
        pid = str(p["partner_id"])
        h = habit_map.get(pid)
        total_habits = int(h["total_habits"]) if h else 0
        done_habits  = int(h["done_habits"])  if h else 0

        can_nudge = (
            p["status"] == "approved"
            and nudge_window_open
        )

        my_keep    = p["requester_keep"] if p["direction"] == "sent" else p["partner_keep"]
        their_keep = p["partner_keep"]   if p["direction"] == "sent" else p["requester_keep"]

        result.append({
            "pair_id":         p["id"],
            "status":          p["status"],
            "assignment_type": p["assignment_type"],
            "direction":       p["direction"],
            "week_start":      str(p["week_start"]) if p["week_start"] else None,
            "approved_at":     p["approved_at"].isoformat() if p["approved_at"] else None,
            "keep_deadline":   p["keep_deadline"].isoformat() if p["keep_deadline"] else None,
            "my_keep_vote":    my_keep,
            "their_keep_vote": their_keep,
            "partner": {
                "id":                    pid,
                "name":                  p["partner_name"],
                "pic":                   p["partner_pic"],
                "steps_today":           steps_map.get(pid, 0),
                "step_streak_days":      streak_map.get(pid, 0),
                "has_steps_today":       steps_map.get(pid, 0) > 0,
                "habits_total":          total_habits,
                "habits_done":           done_habits,
                "habits_pct":            round(done_habits / total_habits * 100) if total_habits else 0,
                "habit_streak_days":     habit_streak_map.get(pid, 0),
                "has_habit_challenge":   total_habits > 0,
                "habits_all_done":       total_habits > 0 and done_habits >= total_habits,
                "habit_challenge":       {
                    "challenge_id":    hc["challenge_id"],
                    "pack_id":         hc["pack_id"],
                    "started_at":      str(hc["started_at"]),
                    "ends_at":         str(hc["ends_at"]),
                    "days_remaining":  hc["days_remaining"],
                } if (hc := habit_challenge_map.get(pid)) else None,
            },
            "unread_messages": unread_map.get(p["id"], 0),
            "can_nudge":       can_nudge,
        })

    return {
        "partners":        result,
        "seeking_partner": user.seeking_partner,
        "partner_opt_out": user.partner_opt_out,
    }


@router.post("/request", status_code=201)
async def request_partner(
    body: PartnerRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send an accountability partner request to another user (same department only)."""
    target_id = str(body.user_id)
    if target_id == str(user.id):
        raise HTTPException(400, "Cannot partner with yourself")

    # Verify target exists
    target_row = await db.execute(select(User).where(User.id == target_id))
    target_user = target_row.scalar_one_or_none()
    if not target_user:
        raise HTTPException(404, "User not found")

    # Same department check
    if str(target_user.department_id) != str(user.department_id):
        raise HTTPException(403, "Partners must be in the same department")

    # Check for existing relationship (either direction)
    existing = await db.execute(text("""
        SELECT id, status FROM accountability_partners
        WHERE  (requester_id = :a AND partner_id = :b)
            OR (requester_id = :b AND partner_id = :a)
          AND  status NOT IN ('completed', 'reshuffled')
    """), {"a": str(user.id), "b": target_id})
    row = existing.mappings().first()
    if row:
        if row["status"] == "blocked":
            raise HTTPException(403, "Cannot send request to this user")
        raise HTTPException(409, f"Relationship already exists (status: {row['status']})")

    await db.execute(text("""
        INSERT INTO accountability_partners (requester_id, partner_id, status, assignment_type)
        VALUES (:req, :par, 'pending', 'manual')
    """), {"req": str(user.id), "par": target_id})

    # Notify target in inbox
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


@router.post("/find-random")
async def find_random_partner(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Self-service: find a random active partner from the user's department.

    Flow:
      1. Always clears opt-out (user is actively looking)
      2. If someone in the same dept is already in the queue → match immediately (201)
      3. If no one waiting → enter queue, return 202 with friendly message
         UI should listen on WebSocket for 'partner_matched' event

    Returns 201:
      { "status": "matched", "pair_id": 42, "partner": { "id": "uuid", "name": "Priya" } }

    Returns 202:
      { "status": "queued", "message": "We're finding you the perfect partner! We'll notify you as soon as someone is ready." }

    Errors:
      409 already_have_partner
    """
    uid     = str(user.id)
    dept_id = str(user.department_id)

    # 1. Check user doesn't already have an active partner
    existing = (await db.execute(text("""
        SELECT id FROM accountability_partners
        WHERE  status IN ('approved', 'pending')
          AND  (requester_id = :uid OR partner_id = :uid)
        LIMIT 1
    """), {"uid": uid})).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "already_have_partner")

    # Clear opt-out — user is actively seeking
    user.partner_opt_out = False

    # 2. Check if someone from same dept is waiting in the queue (FIFO — oldest first)
    waiter = (await db.execute(text("""
        SELECT id, name FROM users
        WHERE  seeking_partner   = true
          AND  department_id     = :dept
          AND  id               != :uid
          AND  id NOT IN (
              SELECT requester_id FROM accountability_partners WHERE status IN ('approved','pending')
              UNION
              SELECT partner_id   FROM accountability_partners WHERE status IN ('approved','pending')
          )
        ORDER BY seeking_since ASC
        LIMIT 1
    """), {"dept": dept_id, "uid": uid})).mappings().first()

    if waiter:
        # Match immediately
        partner_id   = str(waiter["id"])
        partner_name = (waiter["name"] or "Someone").split()[0]
        my_name      = (user.name or "Someone").split()[0]
        today        = date.today()
        week_start   = today - timedelta(days=today.weekday())

        # Clear waiter's queue flag
        await db.execute(text("""
            UPDATE users SET seeking_partner = false, seeking_since = NULL WHERE id = :pid
        """), {"pid": partner_id})

        pair_id = (await db.execute(text("""
            INSERT INTO accountability_partners
                (requester_id, partner_id, status, assignment_type, approved_at, week_start)
            VALUES (:req, :par, 'approved', 'auto', now(), :ws)
            RETURNING id
        """), {"req": uid, "par": partner_id, "ws": week_start})).scalar()

        # Commit pair before notifications
        await db.commit()

        # Notify both — inbox + push
        # write_inbox can silently fail and leave the transaction aborted.
        # Rollback after each call so the next DB query starts on a clean transaction.
        for (notify_uid, other_id, other_name) in [
            (uid,        partner_id, partner_name),
            (partner_id, uid,        my_name),
        ]:
            try:
                await write_inbox(
                    db,
                    user_id=notify_uid,
                    type="partner_assigned",
                    template_key="partner_assigned_v1",
                    payload={"partner_name": other_name, "partner_id": other_id, "assigned_by": "system"},
                    action_url="/socialapp/partners",
                    actor_user_id=other_id,
                    actor_name=other_name,
                )
                await db.commit()
            except Exception:
                await db.rollback()

            # Push if offline, WS event if online — always runs regardless of inbox result
            ws_payload = json.dumps({
                "type":         "partner_matched",
                "pair_id":      pair_id,
                "partner_id":   other_id,
                "partner_name": other_name,
            })
            if is_online(notify_uid):
                try:
                    await db.execute(
                        text("SELECT pg_notify(:ch, :payload)"),
                        {"ch": f"user_{notify_uid}", "payload": ws_payload},
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
            else:
                try:
                    subs = (await db.execute(
                        select(PushSubscription).where(PushSubscription.user_id == notify_uid)
                    )).scalars().all()
                    for sub in subs:
                        result, _ = send_web_push(
                            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                            {
                                "title": "Partner found!",
                                "body":  f"You've been matched with {other_name}. Say hello!",
                                "url":   "/socialapp/partners",
                            },
                        )
                        if result == PushResult.EXPIRED:
                            await db.delete(sub)
                    await db.commit()
                except Exception:
                    await db.rollback()
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=201,
            content={"status": "matched", "pair_id": pair_id, "partner": {"id": partner_id, "name": waiter["name"]}},
        )

    # 3. No one waiting — enter queue
    user.seeking_partner = True
    user.seeking_since   = datetime.now(_IST)
    await db.commit()

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={
            "status":  "queued",
            "message": "We're finding you the perfect partner! We'll notify you as soon as someone is ready.",
        },
    )


@router.delete("/queue", status_code=200)
async def cancel_partner_queue(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel waiting in the partner queue."""
    if not user.seeking_partner:
        raise HTTPException(400, "You are not in the queue")
    user.seeking_partner = False
    user.seeking_since   = None
    await db.commit()
    return {"status": "ok", "message": "You've been removed from the partner queue"}


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


@router.patch("/{partner_id}/keep-vote")
async def keep_vote(
    partner_id: int,
    body: KeepVoteBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Vote to keep or change your weekly partner.
    Available Fri–Sun before keep_deadline.
    Both must vote True → pair renewed Monday.
    Either votes False OR silence → new partner Monday.
    """
    rec = (await db.execute(text("""
        SELECT id, requester_id, partner_id, status, keep_deadline,
               requester_keep, partner_keep
        FROM   accountability_partners
        WHERE  id = :pid
    """), {"pid": partner_id})).mappings().first()

    if not rec:
        raise HTTPException(404, "Partner not found")
    if rec["status"] != "approved":
        raise HTTPException(409, "Pair is not active")

    uid = str(user.id)
    if uid not in (str(rec["requester_id"]), str(rec["partner_id"])):
        raise HTTPException(403, "Not your partnership")

    # Check deadline
    if rec["keep_deadline"] and datetime.now(_IST) > rec["keep_deadline"].replace(tzinfo=_IST):
        raise HTTPException(409, "Voting window has closed")

    if uid == str(rec["requester_id"]):
        col = "requester_keep"
    else:
        col = "partner_keep"

    await db.execute(text(f"""
        UPDATE accountability_partners SET {col} = :vote WHERE id = :pid
    """), {"vote": body.keep, "pid": partner_id})
    await db.commit()

    # Re-fetch to check if both voted
    updated = (await db.execute(text("""
        SELECT requester_keep, partner_keep FROM accountability_partners WHERE id = :pid
    """), {"pid": partner_id})).mappings().first()

    both_keep = updated["requester_keep"] is True and updated["partner_keep"] is True
    return {"status": "ok", "your_vote": body.keep, "both_keep": both_keep}


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

    # 3. Insert nudge event (no uniqueness limit — user can nudge any number of times)
    await db.execute(text("""
        INSERT INTO partner_nudge_events (sender_id, receiver_id, local_day)
        VALUES (:sender, :receiver, :day)
    """), {"sender": sender_id, "receiver": receiver_id, "day": local_day})
    await db.commit()

    sender_name = (user.name or "Your partner").split()[0]
    push_title  = f"{sender_name} is cheering you on"
    push_body   = "You have habits to complete today. Finish them strong!"

    try:
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
        await db.commit()
    except Exception:
        await db.rollback()

    # Deliver: WS if online, push if offline
    push_sent = 0
    ws_payload = json.dumps({
        "type":        "partner_nudge",
        "sender_id":   sender_id,
        "sender_name": sender_name,
        "body":        push_body,
    })

    if is_online(receiver_id):
        try:
            await db.execute(
                text("SELECT pg_notify(:ch, :payload)"),
                {"ch": f"user_{receiver_id}", "payload": ws_payload},
            )
            await db.commit()
        except Exception:
            await db.rollback()
    else:
        try:
            subs_result = await db.execute(
                select(PushSubscription).where(PushSubscription.user_id == receiver_id)
            )
            for sub in subs_result.scalars().all():
                result, _ = send_web_push(
                    {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                    {"title": push_title, "body": push_body, "url": "/socialapp/habits"},
                )
                if result == PushResult.OK:
                    push_sent += 1
                elif result == PushResult.EXPIRED:
                    await db.delete(sub)
            await db.commit()
        except Exception:
            await db.rollback()

    return {"status": "ok", "push_sent": push_sent}


# ── Chat ──────────────────────────────────────────────────────────────────────

@router.get("/{partner_id}/messages")
async def get_messages(
    partner_id: int,
    before_id: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Fetch chat messages for a partner pair.
    Pagination: pass before_id to get messages older than that id.
    Also marks all received messages as read.
    """
    # Verify user belongs to this pair
    pair = (await db.execute(text("""
        SELECT id, requester_id, partner_id, status
        FROM accountability_partners WHERE id = :pid
    """), {"pid": partner_id})).mappings().first()

    if not pair:
        raise HTTPException(404, "Partnership not found")

    uid = str(user.id)
    if uid not in (str(pair["requester_id"]), str(pair["partner_id"])):
        raise HTTPException(403, "Not your partnership")

    # Mark received messages as read
    await db.execute(text("""
        UPDATE partner_messages
        SET read_at = now()
        WHERE pair_id = :pid AND receiver_id = :uid AND read_at IS NULL
    """), {"pid": partner_id, "uid": uid})

    cursor_clause = "AND id < :before_id" if before_id else ""
    rows = (await db.execute(text(f"""
        SELECT pm.id, pm.sender_id, pm.receiver_id, pm.body, pm.sent_at, pm.read_at,
               u.name AS sender_name
        FROM   partner_messages pm
        JOIN   users u ON u.id = pm.sender_id
        WHERE  pm.pair_id = :pid
          {cursor_clause}
        ORDER  BY pm.sent_at DESC
        LIMIT  :lim
    """), {"pid": partner_id, "before_id": before_id, "lim": min(limit, 100)})).mappings().all()

    await db.commit()
    messages = [
        {
            "id":          r["id"],
            "sender_id":   str(r["sender_id"]),
            "sender_name": r["sender_name"],
            "body":        r["body"],
            "sent_at":     r["sent_at"].isoformat(),
            "read_at":     r["read_at"].isoformat() if r["read_at"] else None,
            "is_mine":     str(r["sender_id"]) == uid,
        }
        for r in reversed(rows)   # oldest first for display
    ]
    return {"pair_id": partner_id, "messages": messages}


@router.post("/{partner_id}/messages", status_code=201)
async def send_message(
    partner_id: int,
    body: SendMessageBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Send a chat message to your partner.
    If partner is online (WebSocket) → deliver via pg_notify (no push).
    If offline → send push notification.
    """
    if not body.body.strip():
        raise HTTPException(400, "Message body cannot be empty")
    if len(body.body) > 2000:
        raise HTTPException(400, "Message too long (max 2000 chars)")

    # Verify pair membership
    pair = (await db.execute(text("""
        SELECT id, requester_id, partner_id, status
        FROM accountability_partners WHERE id = :pid
    """), {"pid": partner_id})).mappings().first()

    if not pair:
        raise HTTPException(404, "Partnership not found")

    uid = str(user.id)
    if uid not in (str(pair["requester_id"]), str(pair["partner_id"])):
        raise HTTPException(403, "Not your partnership")
    if pair["status"] not in ("approved",):
        raise HTTPException(409, "Partnership is not active")

    receiver_id = (
        str(pair["partner_id"]) if uid == str(pair["requester_id"])
        else str(pair["requester_id"])
    )

    # Save message
    msg_id = (await db.execute(text("""
        INSERT INTO partner_messages (pair_id, sender_id, receiver_id, body)
        VALUES (:pid, :sender, :receiver, :body)
        RETURNING id
    """), {"pid": partner_id, "sender": uid, "receiver": receiver_id, "body": body.body.strip()})).scalar()

    sender_name = (user.name or "Partner").split()[0]

    # Deliver: WebSocket if online, push if offline
    ws_payload = json.dumps({
        "type":        "chat_message",
        "pair_id":     partner_id,
        "message_id":  msg_id,
        "sender_id":   uid,
        "sender_name": sender_name,
        "body":        body.body.strip(),
    })

    if is_online(receiver_id):
        # pg_notify → WS listener picks it up
        await db.execute(
            text("SELECT pg_notify(:ch, :payload)"),
            {"ch": f"user_{receiver_id}", "payload": ws_payload},
        )
    else:
        await _push_partner_message(db, receiver_id, sender_name, body.body.strip())

    await db.commit()
    return {"status": "ok", "message_id": msg_id}
