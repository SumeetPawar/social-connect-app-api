# backend/routes/admin.py

import random
import logging
from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.security import hash_password
from app.db.deps import get_db
from app.models import User
from app.services.notification_service import write_inbox

router = APIRouter(prefix="/api/admin", tags=["admin"])

class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    is_email_verified: bool
    created_at: str
    
    class Config:
        from_attributes = True


class AdminResetPasswordRequest(BaseModel):
    user_id: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    message: str
    user_email: str
    

# ============================================
# HELPER FUNCTION
# ============================================

def require_admin(current_user: User):
    """Check if current user is admin"""
    if current_user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )


# ============================================
# ENDPOINTS
# ============================================

@router.get("/users", response_model=List[UserResponse])
async def get_all_users(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of all users (admin only)
    """
    require_admin(current_user)

    # Return only users in the same department as the admin
    result = await db.execute(
        select(User)
        .where(User.department_id == current_user.department_id)
        .order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    
    return [
        UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            is_email_verified=user.is_email_verified,
            created_at=user.created_at.isoformat()
        )
        for user in users
    ]


@router.post("/users/reset-password", response_model=ResetPasswordResponse)
async def admin_reset_user_password(
    request: AdminResetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Reset password for any user (admin only)
    """
    logging.info(f"[ADMIN] {current_user.email} (ID: {current_user.id}) is attempting to reset password for user_id={request.user_id}")
    require_admin(current_user)
    
    # Validate password length
    if len(request.new_password) < 7:
        logging.warning(f"[ADMIN] {current_user.email} tried to set a too-short password for user_id={request.user_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 7 characters"
        )
    
    # Find target user using async select
    result = await db.execute(
        select(User).where(User.id == request.user_id)
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        logging.error(f"[ADMIN] {current_user.email} tried to reset password for non-existent user_id={request.user_id}")
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from resetting other admin passwords (optional security)
    # if target_user.role == 'admin' and str(target_user.id) != str(current_user.id):
    #     raise HTTPException(status_code=403, detail="Cannot reset other admin passwords")
    
    # Update password
    logging.info(f"[ADMIN] {current_user.email} is resetting password for {target_user.email} (ID: {target_user.id})")
    target_user.password_hash = hash_password(request.new_password)
    await db.commit()
    logging.info(f"[ADMIN] Password reset successful for {target_user.email} (ID: {target_user.id}) by {current_user.email}")
    
    # TODO: Send email notification later
    # send_password_changed_notification(target_user.email)
    
    return ResetPasswordResponse(
        message="Password reset successfully",
        user_email=target_user.email
    )


# ============================================
# PARTNER MANAGEMENT (ADMIN)
# ============================================

class AssignPartnerRequest(BaseModel):
    user_id: str
    partner_id: str


async def _close_active_pair(db: AsyncSession, user_id: str, admin_id: str) -> None:
    """Mark any existing approved/pending pair for this user as reshuffled."""
    result = await db.execute(text("""
        UPDATE accountability_partners
        SET    status = 'reshuffled'
        WHERE  status IN ('approved', 'pending')
          AND  (requester_id = :uid OR partner_id = :uid)
        RETURNING id
    """), {"uid": user_id})
    pair_ids = [r[0] for r in result.fetchall()]
    # Set expiry on messages for closed pairs
    for pid in pair_ids:
        await db.execute(text("""
            UPDATE partner_messages
            SET expires_at = now() + INTERVAL '30 days'
            WHERE pair_id = :pid AND expires_at IS NULL
        """), {"pid": pid})


def _this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


@router.get("/partners")
async def admin_list_partners(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all active partner pairs in the admin's department.
    Also shows users with no current partner.
    """
    require_admin(current_user)
    dept_id = str(current_user.department_id)

    pairs = (await db.execute(text("""
        SELECT
            ap.id,
            ap.status,
            ap.assignment_type,
            ap.week_start,
            ap.approved_at,
            ap.requester_keep,
            ap.partner_keep,
            u1.id   AS user_a_id,
            u1.name AS user_a_name,
            u2.id   AS user_b_id,
            u2.name AS user_b_name,
            (now() - COALESCE(ap.approved_at, ap.created_at))::text AS together_since
        FROM accountability_partners ap
        JOIN users u1 ON u1.id = ap.requester_id
        JOIN users u2 ON u2.id = ap.partner_id
        WHERE ap.status IN ('approved', 'pending')
          AND u1.department_id = :dept
          AND u2.department_id = :dept
        ORDER BY ap.approved_at DESC NULLS LAST
    """), {"dept": dept_id})).mappings().all()

    # Users in this dept that have NO active partner
    unmatched = (await db.execute(text("""
        SELECT u.id, u.name
        FROM users u
        WHERE u.department_id = :dept
          AND u.id NOT IN (
              SELECT requester_id FROM accountability_partners WHERE status IN ('approved','pending')
              UNION
              SELECT partner_id   FROM accountability_partners WHERE status IN ('approved','pending')
          )
        ORDER BY u.name
    """), {"dept": dept_id})).mappings().all()

    return {
        "pairs": [dict(p) for p in pairs],
        "unmatched_users": [{"id": str(u["id"]), "name": u["name"]} for u in unmatched],
    }


@router.post("/partners/assign", status_code=201)
async def admin_assign_partner(
    body: AssignPartnerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually assign a specific accountability partner pair.
    Both users must be in the admin's department.
    Any existing active pair for either user is marked reshuffled.
    New pair is created as approved immediately.
    """
    require_admin(current_user)
    dept_id = str(current_user.department_id)

    if body.user_id == body.partner_id:
        raise HTTPException(400, "Cannot pair a user with themselves")

    # Verify both users exist and belong to admin's dept
    for uid in (body.user_id, body.partner_id):
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not u:
            raise HTTPException(404, f"User {uid} not found")
        if str(u.department_id) != dept_id:
            raise HTTPException(403, f"User {uid} is not in your department")

    # Close existing pairs for both users
    await _close_active_pair(db, body.user_id, str(current_user.id))
    await _close_active_pair(db, body.partner_id, str(current_user.id))

    # Create new approved pair
    pair_id = (await db.execute(text("""
        INSERT INTO accountability_partners
            (requester_id, partner_id, status, assignment_type, assigned_by, approved_at, week_start)
        VALUES (:a, :b, 'approved', 'admin', :admin, now(), :ws)
        RETURNING id
    """), {
        "a": body.user_id, "b": body.partner_id,
        "admin": str(current_user.id),
        "ws": _this_monday(),
    })).scalar()

    admin_name = (current_user.name or "Admin").split()[0]
    # Notify both users
    for uid in (body.user_id, body.partner_id):
        other_id = body.partner_id if uid == body.user_id else body.user_id
        other_user = (await db.execute(select(User).where(User.id == other_id))).scalar_one_or_none()
        other_name = (other_user.name or "Someone").split()[0] if other_user else "Someone"
        await write_inbox(
            db,
            user_id=uid,
            type="partner_assigned",
            template_key="partner_assigned_v1",
            payload={"partner_name": other_name, "partner_id": other_id, "assigned_by": admin_name},
            action_url="/socialapp/partners",
            actor_user_id=str(current_user.id),
            actor_name=admin_name,
        )

    await db.commit()
    return {"status": "ok", "pair_id": pair_id}


@router.post("/partners/shuffle")
async def admin_shuffle_partners(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Auto-shuffle all users in the admin's department into new partner pairs.

    Activity check: user is "active" if they have step/habit data in last 7 days
    OR joined within 7 days (new user treated as active).

    Algorithm:
      1. Partition users into active / inactive pools
      2. Shuffle active pool randomly
      3. Pair active users: [0,1], [2,3], ...
      4. Odd active: last active gets paired with first inactive
      5. Remaining inactive: pair among themselves
    """
    require_admin(current_user)
    dept_id = str(current_user.department_id)

    # Fetch all users in dept
    users_rows = (await db.execute(text("""
        SELECT u.id, u.name, u.created_at,
               EXISTS (
                   SELECT 1 FROM daily_steps ds
                   WHERE ds.user_id = u.id AND ds.day >= current_date - 7
               ) OR EXISTS (
                   SELECT 1 FROM daily_logs dl
                   JOIN   habit_commitments hcm ON hcm.id = dl.commitment_id
                   JOIN   habit_challenges  hc  ON hc.id  = hcm.challenge_id
                   WHERE  hc.user_id = u.id AND dl.logged_date >= current_date - 7
               ) OR u.created_at >= now() - INTERVAL '7 days' AS is_active
        FROM users u
        WHERE u.department_id = :dept
        ORDER BY u.created_at
    """), {"dept": dept_id})).mappings().all()

    if len(users_rows) < 2:
        raise HTTPException(400, "Need at least 2 users in department to shuffle")

    active   = [dict(r) for r in users_rows if r["is_active"]]
    inactive = [dict(r) for r in users_rows if not r["is_active"]]

    random.shuffle(active)
    random.shuffle(inactive)

    pairs_to_create: list[tuple[str, str]] = []
    unmatched_ids: list[str] = []

    # Pair active users
    i = 0
    while i + 1 < len(active):
        pairs_to_create.append((str(active[i]["id"]), str(active[i + 1]["id"])))
        i += 2

    # Odd active user gets first inactive
    if i < len(active):
        if inactive:
            pairs_to_create.append((str(active[i]["id"]), str(inactive.pop(0)["id"])))
        else:
            unmatched_ids.append(str(active[i]["id"]))

    # Pair remaining inactive among themselves
    j = 0
    while j + 1 < len(inactive):
        pairs_to_create.append((str(inactive[j]["id"]), str(inactive[j + 1]["id"])))
        j += 2
    if j < len(inactive):
        unmatched_ids.append(str(inactive[j]["id"]))

    # Close all existing pairs + create new ones
    reshuffled_count = 0
    admin_name = (current_user.name or "Admin").split()[0]
    week_start = _this_monday()

    for uid in [str(r["id"]) for r in users_rows]:
        result = await db.execute(text("""
            UPDATE accountability_partners
            SET status = 'reshuffled'
            WHERE status IN ('approved', 'pending')
              AND (requester_id = :uid OR partner_id = :uid)
            RETURNING id
        """), {"uid": uid})
        for row in result.fetchall():
            reshuffled_count += 1
            await db.execute(text("""
                UPDATE partner_messages
                SET expires_at = now() + INTERVAL '30 days'
                WHERE pair_id = :pid AND expires_at IS NULL
            """), {"pid": row[0]})

    for (uid_a, uid_b) in pairs_to_create:
        pair_id = (await db.execute(text("""
            INSERT INTO accountability_partners
                (requester_id, partner_id, status, assignment_type, assigned_by, approved_at, week_start)
            VALUES (:a, :b, 'approved', 'auto', :admin, now(), :ws)
            ON CONFLICT (requester_id, partner_id) DO UPDATE
                SET status = 'approved', assignment_type = 'auto',
                    assigned_by = :admin, approved_at = now(), week_start = :ws
            RETURNING id
        """), {"a": uid_a, "b": uid_b, "admin": str(current_user.id), "ws": week_start})).scalar()

        # Notify both
        for uid in (uid_a, uid_b):
            other_id = uid_b if uid == uid_a else uid_a
            other_user = (await db.execute(select(User).where(User.id == other_id))).scalar_one_or_none()
            other_name = (other_user.name or "Someone").split()[0] if other_user else "Someone"
            await write_inbox(
                db,
                user_id=uid,
                type="partner_assigned",
                template_key="partner_assigned_v1",
                payload={"partner_name": other_name, "partner_id": other_id, "assigned_by": admin_name},
                action_url="/socialapp/partners",
                actor_user_id=str(current_user.id),
                actor_name=admin_name,
            )

    await db.commit()

    return {
        "status": "ok",
        "pairs_created": len(pairs_to_create),
        "reshuffled_old": reshuffled_count // 2,  # approximate unique pairs
        "unmatched": unmatched_ids,
    }