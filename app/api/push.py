from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Literal
import logging

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User
from app.models import PushSubscription
from app.schemas.push import PushSubscriptionRequest, PushNotificationRequest
from app.services.push_notify import send_web_push, PushResult

router = APIRouter(prefix="/api/push", tags=["push"])
logger = logging.getLogger(__name__)


def _is_legacy_endpoint(endpoint: str) -> bool:
    """Old FCM format (fcm.googleapis.com/fcm/send/…) — deprecated June 2024, never delivers."""
    return "fcm.googleapis.com/fcm/send/" in endpoint


@router.post("/subscribe")
async def subscribe_push(
    payload: PushSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Register a push subscription.
    Replaces ALL previous subscriptions for this user with the new one.
    This ensures the DB never holds stale/legacy endpoints.
    """
    # Delete all old subscriptions for this user, then insert fresh one.
    # This keeps the DB clean — one active device per registration call.
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id))

    db.add(PushSubscription(
        user_id=user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.get("p256dh"),
        auth=payload.keys.get("auth"),
    ))
    await db.commit()
    logger.info(f"Push subscription saved for user {user.id}: {payload.endpoint[:60]}...")
    return {"status": "ok", "message": "Push subscription registered"}


@router.delete("/unsubscribe")
async def unsubscribe_push(
    endpoint: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Unregister a push subscription"""
    stmt = delete(PushSubscription).where(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == endpoint
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok", "message": "Push subscription removed"}


@router.post("/test")
async def test_push_notification(
    payload: PushNotificationRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a test push notification to the current user"""
    target_user_id = payload.user_id or user.id

    # Get all subscriptions for the target user
    stmt = select(PushSubscription).where(PushSubscription.user_id == target_user_id)
    result = await db.execute(stmt)
    subscriptions = result.scalars().all()

    if not subscriptions:
        raise HTTPException(status_code=404, detail="No push subscriptions found for user")

    # Send notification; auto-delete expired subscriptions
    success_count = 0
    for sub in subscriptions:
        result = send_web_push(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            {"title": payload.title, "body": payload.body},
        )
        if result == PushResult.OK:
            success_count += 1
        elif result == PushResult.EXPIRED:
            logger.info(f"Deleting expired subscription {sub.id} for user {target_user_id}")
            await db.delete(sub)
            await db.commit()

    return {
        "status": "ok",
        "message": f"Sent to {success_count}/{len(subscriptions)} subscriptions"
    }


@router.post("/trigger/{job}")
async def trigger_notification_job(
    job: Literal["step_reminder", "streak_at_risk", "rank_changes", "weekly_summary"],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Admin/dev endpoint — manually fire any scheduled notification job immediately.
    Useful for testing without waiting for the cron schedule.

    job options:
      - step_reminder   → evening step reminder for all users with no steps today
      - streak_at_risk  → streak protection alert
      - rank_changes    → rank up/down notifications after snapshot
      - weekly_summary  → weekly step + challenge summary
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.reminder_service import (
        send_step_reminders,
        send_streak_at_risk,
        send_rank_change_notifications,
        send_weekly_summary,
    )

    job_map = {
        "step_reminder":  send_step_reminders,
        "streak_at_risk": send_streak_at_risk,
        "rank_changes":   send_rank_change_notifications,
        "weekly_summary": send_weekly_summary,
    }

    count = await job_map[job](db)
    return {"status": "ok", "job": job, "users_notified": count}
