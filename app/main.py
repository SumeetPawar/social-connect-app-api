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
from app.services.scheduler import start_scheduler, stop_scheduler

import logging
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start the reminder scheduler
    start_scheduler()
    yield
    # Shutdown: Stop the scheduler
    stop_scheduler()


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
