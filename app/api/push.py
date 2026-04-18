from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from typing import Literal
import logging
from datetime import date, timedelta

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
    job: Literal[
        "step_reminder", "streak_at_risk",
        "rank_changes", "weekly_summary",
        "habit_morning", "habit_evening", "challenge_nudge",
        "habit_cycle_summary", "nightly_insights",
    ],
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
        send_habit_morning_reminder,
        send_habit_evening_nudge,
        send_challenge_step_nudges,
        send_habit_cycle_summary,
    )
    from app.services.ai_insight import generate_nightly_insights

    job_map = {
        "step_reminder":      send_step_reminders,
        "streak_at_risk":     send_streak_at_risk,
        "rank_changes":       send_rank_change_notifications,
        "weekly_summary":     send_weekly_summary,
        "habit_morning":      send_habit_morning_reminder,
        "habit_evening":      send_habit_evening_nudge,
        "challenge_nudge":    send_challenge_step_nudges,
        "habit_cycle_summary": send_habit_cycle_summary,
        "nightly_insights":   generate_nightly_insights,
    }

    count = await job_map[job](db)
    return {"status": "ok", "job": job, "users_notified": count}


@router.get("/logs")
async def push_delivery_logs(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Admin dashboard — notification delivery log for the last N days.
    Shows every push attempt: job name, result (ok/expired/error), title, timestamp.

    GET /api/push/logs          → last 7 days
    GET /api/push/logs?days=30  → last 30 days
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    since = date.today() - timedelta(days=days)

    # Per-job success rate summary
    summary_rows = await db.execute(text("""
        SELECT
            job,
            COUNT(*)                                          AS total,
            COUNT(*) FILTER (WHERE result = 'ok')            AS delivered,
            COUNT(*) FILTER (WHERE result = 'expired')       AS expired,
            COUNT(*) FILTER (WHERE result = 'error')         AS errors,
            MAX(sent_at)                                      AS last_fired
        FROM push_logs
        WHERE sent_at >= :since
        GROUP BY job
        ORDER BY last_fired DESC
    """), {"since": since})

    summary = [
        {
            "job":        r["job"],
            "total":      r["total"],
            "delivered":  r["delivered"],
            "expired":    r["expired"],
            "errors":     r["errors"],
            "success_pct": round(r["delivered"] / r["total"] * 100) if r["total"] else 0,
            "last_fired": r["last_fired"].isoformat() if r["last_fired"] else None,
        }
        for r in summary_rows.mappings().all()
    ]

    # Per-day delivery counts
    daily_rows = await db.execute(text("""
        SELECT
            sent_at::date                                     AS day,
            COUNT(*)                                          AS total,
            COUNT(*) FILTER (WHERE result = 'ok')            AS delivered,
            COUNT(*) FILTER (WHERE result = 'error')         AS errors
        FROM push_logs
        WHERE sent_at >= :since
        GROUP BY sent_at::date
        ORDER BY day DESC
    """), {"since": since})

    daily = [
        {
            "day":       str(r["day"]),
            "total":     r["total"],
            "delivered": r["delivered"],
            "errors":    r["errors"],
        }
        for r in daily_rows.mappings().all()
    ]

    # Recent individual log entries
    recent_rows = await db.execute(text("""
        SELECT pl.job, pl.result, pl.title, pl.sent_at, u.name AS user_name
        FROM push_logs pl
        JOIN users u ON u.id = pl.user_id
        WHERE pl.sent_at >= :since
        ORDER BY pl.sent_at DESC
        LIMIT 100
    """), {"since": since})

    recent = [
        {
            "job":       r["job"],
            "result":    r["result"],
            "title":     r["title"],
            "user":      r["user_name"],
            "sent_at":   r["sent_at"].isoformat(),
        }
        for r in recent_rows.mappings().all()
    ]

    total_sent = sum(s["total"] for s in summary)
    total_ok   = sum(s["delivered"] for s in summary)

    return {
        "period_days":    days,
        "overall": {
            "total_attempts": total_sent,
            "delivered":      total_ok,
            "success_pct":    round(total_ok / total_sent * 100) if total_sent else 0,
        },
        "by_job":  summary,
        "by_day":  daily,
        "recent":  recent,
    }
