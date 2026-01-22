from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, date
from zoneinfo import ZoneInfo

from app.models.user import User
from app.models.daily_total import DailyTotal
from app.models.push_subscription import PushSubscription
from app.services.push_notify import send_web_push
import logging

logger = logging.getLogger(__name__)


async def send_step_reminders(db: AsyncSession):
    """
    Send push notifications to users who haven't logged steps today.
    Only sends after 9 AM in the user's timezone.
    """
    logger.info("Checking users for step reminders")
    logger.info(f"Current UTC time: {datetime.now().isoformat()}")
    logger.info(f"Current server local time: {datetime.now().isoformat()}")
    # Get all users with push subscriptions
    stmt = select(User).join(PushSubscription, User.id == PushSubscription.user_id).distinct()
    logger.debug(f"Executing query to find users with push subscriptions: {stmt}")
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    logger.info(f"Found {len(users)} users with push subscriptions")
    
    reminder_count = 0

    for user in users:
        try:
            # Check if it's after 9 AM in user's timezone
            tz = ZoneInfo(user.timezone or "Asia/Kolkata")
            now = datetime.now(tz)
            today = now.date()
            
            # Skip if before 9 AM
            if now.hour < 9:
                logger.debug(f"Skipping user {user.name or user.email} (ID: {user.id}) - before 9 AM in their timezone")
                continue

            # Check if user has logged steps today
            stmt = select(DailyTotal).where(
                DailyTotal.user_id == user.id,
                DailyTotal.day == today
            )
            result = await db.execute(stmt)
            daily_total = result.scalar_one_or_none()

            # If no steps logged, send notification
            if not daily_total:
                logger.info(f"User {user.name or user.email} (ID: {user.id}) has not logged steps today - sending reminder")
                
                # Get all push subscriptions for this user
                stmt = select(PushSubscription).where(PushSubscription.user_id == user.id)
                result = await db.execute(stmt)
                subscriptions = result.scalars().all()

                # Send notification to all devices
                sent_count = 0
                for sub in subscriptions:
                    try:
                        subscription_info = {
                            "endpoint": sub.endpoint,
                            "keys": {
                                "p256dh": sub.p256dh,
                                "auth": sub.auth
                            }
                        }
                        send_web_push(
                            subscription_info,
                            {
                                "title": "Step Reminder",
                                "body": "Don't forget to log your steps to device"
                            }
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Failed to send push to user {user.name or user.email} (ID: {user.id}): {e}")
                
                if sent_count > 0:
                    reminder_count += 1
                    logger.info(f"Sent reminders to {sent_count} device(s) for user {user.name or user.email} (ID: {user.id})")

        except Exception as e:
            logger.error(f"Error processing user {user.name or user.email} (ID: {user.id}) for step reminders: {e}")
