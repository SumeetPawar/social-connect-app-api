"""
POST /api/feedback    — submit feedback / suggestion
GET  /api/feedback    — list own submissions
"""
from typing import Literal, Optional
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.deps import get_db
from app.models import User, UserFeedback

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    type:   Literal["bug", "suggestion", "general"] = "general"
    title:  str = Field(..., min_length=3, max_length=200)
    body:   Optional[str] = Field(None, max_length=2000)
    rating: Optional[int] = Field(None, ge=1, le=5)
    meta:   Optional[dict] = None   # e.g. {"screen": "home", "app_version": "1.2.0"}


class FeedbackOut(BaseModel):
    id:         int
    type:       str
    title:      str
    body:       Optional[str]
    rating:     Optional[int]
    status:     str
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    body: FeedbackIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = UserFeedback(
        user_id=str(user.id),
        type=body.type,
        title=body.title,
        body=body.body,
        rating=body.rating,
        meta=body.meta,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("", response_model=list[FeedbackOut])
async def my_feedback(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserFeedback)
        .where(UserFeedback.user_id == str(user.id))
        .order_by(UserFeedback.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()
