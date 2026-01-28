from fastapi import FastAPI, Request
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
from app.api.challenges import router as challenges_router
from app.api.goal_definitions import router as goal_definitions_router
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
app.include_router(goal_definitions_router)
app.include_router(challenges_router)

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