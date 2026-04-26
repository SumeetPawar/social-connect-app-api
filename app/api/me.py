from fastapi import APIRouter, Depends
from app.auth.deps import get_current_user
from app.db.deps import get_db
from app.models import User, Department
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.profile import ProfileOut, ProfileUpdate


class MePatch(BaseModel):
    partner_opt_out: Optional[bool] = None

router = APIRouter(prefix="/api/me", tags=["me"])


class DepartmentOut(BaseModel):
    id: str
    name: str


class MeOut(BaseModel):
    """Single comprehensive response — frontend calls /me ONCE and caches it."""
    id: str
    email: str
    name: Optional[str]
    role: str
    # department
    department: Optional[DepartmentOut]
    # profile / body-composition personalisation fields
    age: Optional[int]
    gender: Optional[str]
    activity_level: Optional[str]
    height_cm: Optional[float]

    class Config:
        from_attributes = True


@router.get("", response_model=MeOut)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns identity + profile data in one call.
    Frontend: call this ONCE on app load, store result in a global/context/store,
    and reuse everywhere. Do NOT call /me inside individual components.
    """
    dept = None
    if user.department_id:
        dept_result = await db.execute(
            select(Department).where(Department.id == user.department_id)
        )
        dept_row = dept_result.scalar_one_or_none()
        if dept_row:
            dept = DepartmentOut(id=str(dept_row.id), name=dept_row.name)

    return MeOut(
        id             = str(user.id),
        email          = user.email,
        name           = user.name,
        role           = user.role,
        department     = dept,
        age            = user.age,
        gender         = user.gender,
        activity_level = user.activity_level,
        height_cm      = float(user.height_cm) if user.height_cm is not None else None,
    )


@router.patch("", status_code=200)
async def patch_me(
    data: MePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user-level preferences (partner opt-out, etc.)."""
    if data.partner_opt_out is not None:
        user.partner_opt_out = data.partner_opt_out
    await db.commit()
    return {"status": "ok"}


# ─── /profile kept for backwards compat ──────────────────────────────────────

@router.get("/profile", response_model=ProfileOut)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Subset of /me — prefer using GET /me instead."""
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