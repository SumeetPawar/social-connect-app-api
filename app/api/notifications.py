"""
notifications.py — User-facing notification inbox API.

Endpoints:
  GET    /api/notifications                  — paginated inbox (newest first)
  GET    /api/notifications/unread-count     — bell badge count
  PATCH  /api/notifications/read-all         — mark everything read
  PATCH  /api/notifications/{id}/read        — mark one read
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def get_inbox(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: int | None = Query(default=None, description="Last seen id for cursor pagination"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Fetch the current user's notification inbox, newest first.
    Only returns non-expired rows.
    Use `cursor` (the last `id` from a previous response) to page forward.

    Admin users: weekly_summary items are always pinned at the top (before cursor pagination),
    then remaining items follow in newest-first order.
    """
    is_admin = getattr(user, "role", None) == "admin"
    base_where = "user_id = :uid AND (expires_at IS NULL OR expires_at > now())"
    params: dict = {"uid": str(user.id), "limit": limit + 1}

    select_cols = """
        SELECT id, type, actor_user_id, actor_name, template_key,
               payload, action_url, push_title, push_body,
               is_read, created_at
        FROM   notification_inbox
    """

    pinned: list = []
    if is_admin and cursor is None:
        # First page only: fetch weekly_summary rows pinned at the top
        pinned_rows = await db.execute(text(f"""
            {select_cols}
            WHERE  {base_where} AND type = 'weekly_summary'
            ORDER  BY created_at DESC
        """), {"uid": str(user.id)})
        pinned = [dict(r) for r in pinned_rows.mappings()]

    # Main paginated query — excludes weekly_summary for admins (already pinned)
    where = base_where
    if is_admin:
        where += " AND type != 'weekly_summary'"
    if cursor is not None:
        where += " AND id < :cursor"
        params["cursor"] = cursor

    rows = await db.execute(text(f"""
        {select_cols}
        WHERE  {where}
        ORDER  BY created_at DESC
        LIMIT  :limit
    """), params)

    items = [dict(r) for r in rows.mappings()]
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    return {
        "items":       pinned + items,
        "has_more":    has_more,
        "next_cursor": items[-1]["id"] if has_more else None,
    }


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns the number of unread, non-expired inbox items (for the bell badge)."""
    row = await db.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM   notification_inbox
        WHERE  user_id  = :uid
          AND  is_read  = false
          AND  (expires_at IS NULL OR expires_at > now())
    """), {"uid": str(user.id)})
    return {"unread": row.scalar() or 0}


@router.patch("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark every unread inbox item as read for the current user."""
    await db.execute(text("""
        UPDATE notification_inbox
        SET    is_read = true
        WHERE  user_id = :uid AND is_read = false
    """), {"uid": str(user.id)})
    await db.commit()
    return {"status": "ok"}


@router.patch("/{notification_id}/read")
async def mark_one_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a single inbox item as read. Returns 404 if it doesn't belong to the user."""
    result = await db.execute(text("""
        UPDATE notification_inbox
        SET    is_read = true
        WHERE  id = :nid AND user_id = :uid
        RETURNING id
    """), {"nid": notification_id, "uid": str(user.id)})
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
    return {"status": "ok"}
