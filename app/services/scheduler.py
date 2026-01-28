from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.services.reminder_service import send_step_reminders
import logging

logger = logging.getLogger(__name__)

# Create scheduler instance - will be started from main.py
scheduler = AsyncIOScheduler()


async def check_and_send_reminders():
    """Wrapper to get DB session and send reminders"""
    logger.info("Starting hourly step reminder check")
    async with AsyncSessionLocal() as db:
        try:
            await send_step_reminders(db)
            logger.info("Completed hourly step reminder check")
        except Exception as e:
            logger.error(f"Error in reminder job: {e}", exc_info=True)


# Configure the job when module loads (but don't start scheduler yet)
logger.info("Configuring step reminder job")
scheduler.add_job(
    check_and_send_reminders,
    CronTrigger(hour='9-23', minute=0),  # Every hour on the hour from 9 AM to 11 PM
    id='step_reminders',
    replace_existing=True
)
logger.info("Step reminder job configured - will run every hour from 9 AM to 11 PM")