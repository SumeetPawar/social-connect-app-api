"""
notification_service.py — Centralised helper to write to notification_inbox.

Usage (from any service or API handler):
    from app.services.notification_service import write_inbox

    await write_inbox(
        db,
        user_id=str(user.id),
        type="perfect_day",
        template_key="perfect_day_v1",
        payload={"name": "Rahul", "habit_count": 5},
        action_url="/socialapp/habits",
        push_title="Perfect day, Rahul! 🎉",
        push_body="Every habit done. That's exactly who you're becoming.",
    )

Caller is responsible for committing the session (write_inbox does NOT commit).
For partner nudges the caller commits after also sending the push.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Retention policy ──────────────────────────────────────────────────────────
# None  = never expires (achievements live forever in inbox)
# int   = days until expiry
_EXPIRY_DAYS: dict[str, int | None] = {
    "perfect_day":      None,   # achievement — never expires
    "habit_milestone":  None,   # achievement — never expires
    "rank_up":          30,
    "weekly_summary":   90,
    "habit_cycle":      90,
    "partner_nudge":    30,
    "partner_request":  None,   # keep until actioned (user must see it)
    "partner_accepted": 30,
}
_DEFAULT_EXPIRY_DAYS = 30


async def write_inbox(
    db: AsyncSession,
    *,
    user_id: str,
    type: str,
    template_key: str,
    payload: dict[str, Any],
    action_url: str | None = None,
    push_title: str | None = None,
    push_body: str | None = None,
    actor_user_id: str | None = None,
    actor_name: str | None = None,
) -> None:
    """
    Insert one row into notification_inbox.

    Never raises — errors are logged silently so a failing inbox write
    never breaks the surrounding push or API response.

    The caller must commit the session after calling this function.
    """
    try:
        expiry_days = _EXPIRY_DAYS.get(type, _DEFAULT_EXPIRY_DAYS)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expiry_days)
            if expiry_days is not None
            else None
        )
        await db.execute(text("""
            INSERT INTO notification_inbox
                (user_id, type, actor_user_id, actor_name, template_key,
                 payload, action_url, push_title, push_body, expires_at)
            VALUES
                (:user_id, :type, :actor_user_id, :actor_name, :template_key,
                 CAST(:payload AS jsonb), :action_url, :push_title, :push_body, :expires_at)
        """), {
            "user_id":       str(user_id),
            "type":          type,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "actor_name":    actor_name,
            "template_key":  template_key,
            "payload":       json.dumps(payload),
            "action_url":    action_url,
            "push_title":    push_title,
            "push_body":     push_body,
            "expires_at":    expires_at,
        })
    except Exception as exc:
        logger.error(
            "write_inbox failed for user=%s type=%s: %s", user_id, type, exc
        )
