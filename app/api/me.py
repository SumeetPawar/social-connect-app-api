from fastapi import APIRouter, Depends
from app.auth.deps import get_current_user
from app.db.deps import get_db
from app.models import User
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.profile import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/api/me", tags=["me"])

@router.get("")
async def me(user: User = Depends(get_current_user)):
    return {"id": str(user.id), "email": user.email, "name": user.name,"role": user.role}



# ─── Add these two routes to your existing users router ──────────────────────

@router.get("/profile", response_model=ProfileOut)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return profile fields used for body composition range personalisation."""
    return ProfileOut(
        age            = current_user.age,
        gender         = current_user.gender,
        activity_level = current_user.activity_level,
        height_cm      = current_user.height_cm,
    )


@router.put("/profile", response_model=ProfileOut)
async def update_profile(
    data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update profile fields. Only provided fields are changed."""
    for field, val in data.dict(exclude_unset=True).items():
        setattr(current_user, field, val)
    await db.commit()
    await db.refresh(current_user)
    return current_user