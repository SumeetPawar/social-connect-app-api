from fastapi import FastAPI, Request
from app.api import admin
from app.core.config import settings
from app.api.health import router as health_router
from app.api.ws import router as ws_router
from app.api.debug import router as debug_router
from app.api.auth import router as auth_router
from app.api.me import router as me_router
from app.api.steps import router as steps_router
from app.api.goals import router as goals_router
# from app.api.streaks import router as streaks_router
from app.api.push import router as push_router
from app.api.body_metrics import router as body_metrics_router
from app.api.admin import router as admin_router
from app.api.challenges import router as challenges_router
from app.api.goal_definitions import router as goal_definitions_router
from app.api.habits import habits_router, challenges_router as habit_challenges_router
from app.api.home import router as home_router
from app.api.coach import router as coach_router
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import scheduler
try:
    from app.services.scheduler import scheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    scheduler = None
    logger.warning("Scheduler not available")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("App lifespan starting")
    
    # Start scheduler in the main event loop (not in executor)
    if SCHEDULER_AVAILABLE and scheduler:
        try:
            logger.info("Starting scheduler...")
            # Start scheduler directly - it will use the current event loop
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully")
            # Run rank snapshot immediately on startup (non-blocking)
            from app.services.scheduler import update_all_previous_ranks
            asyncio.create_task(update_all_previous_ranks())
            logger.info("Triggered initial previous_rank snapshot on startup")
            # Send service-started notification to test user
            from app.services.reminder_service import send_service_started_notification
            from app.db.session import AsyncSessionLocal
            async def _notify_startup():
                try:
                    async with AsyncSessionLocal() as db:
                        await send_service_started_notification(db)
                except Exception as e:
                    logger.warning(f"Startup notification skipped (DB may not be reachable): {e}")
            asyncio.create_task(_notify_startup())
            logger.info("Triggered startup notification to test user")
        except Exception as e:
            logger.error(f"Scheduler startup failed: {e}", exc_info=True)
    
    logger.info("App startup complete")
    
    yield
    
    # Shutdown
    logger.info("App shutting down")
    if SCHEDULER_AVAILABLE and scheduler:
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
                logger.info("Scheduler stopped")
        except Exception as e:
            logger.error(f"Scheduler shutdown error: {e}")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(debug_router)
app.include_router(health_router)
app.include_router(ws_router)
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(steps_router)
app.include_router(goals_router)
# app.include_router(streaks_router)
app.include_router(push_router)

app.include_router(body_metrics_router)

app.include_router(goal_definitions_router)
app.include_router(challenges_router)
app.include_router(habits_router)
app.include_router(habit_challenges_router)
app.include_router(home_router)
app.include_router(coach_router)
app.include_router(admin_router)  # Add this line


if __name__ == "__main__":
    import os
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )



#     /api/home  —  single endpoint that feeds the home screen.

# {
#   "steps": { "yesterday": 8240, "today": 0, "daily_target": 8000, "pct": 0, "step_streak": 6 },
#   "challenge": { "id": "...", "rank": 5, "rank_change": 1 },
#   "habits": { "challenge_id": 3, "day_number": 6, "total_days": 21, "completed_count": 3, "total_count": 5, "all_done": false },
#   "habit_streak": 6,
#   "ai_insight": { "badge": "Best Tuesday this month", "headline": "...", "detail": "..." },
#   "user": { "name": "Alex", "profile_pic_url": null }
# }