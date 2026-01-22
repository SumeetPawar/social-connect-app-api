from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models.user import User
from app.models.push_subscription import PushSubscription
from app.schemas.push import PushSubscriptionRequest, PushNotificationRequest
from app.services.push_notify import send_web_push

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/subscribe")
async def subscribe_push(
    payload: PushSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register a push subscription for the current user"""
    # Check if subscription already exists
    stmt = select(PushSubscription).where(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == payload.endpoint
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing subscription
        existing.p256dh = payload.keys.get("p256dh")
        existing.auth = payload.keys.get("auth")
    else:
        # Create new subscription
        subscription = PushSubscription(
            user_id=user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.get("p256dh"),
            auth=payload.keys.get("auth")
        )
        db.add(subscription)

    await db.commit()
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

    # Send notification to all subscriptions
    success_count = 0
    for sub in subscriptions:
        try:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth
                }
            }
            send_web_push(subscription_info, {"title": payload.title, "body": payload.body})
            success_count += 1
        except Exception as e:
            print(f"Failed to send push to subscription {sub.id}: {e}")

    return {
        "status": "ok",
        "message": f"Sent to {success_count}/{len(subscriptions)} subscriptions"
    }
