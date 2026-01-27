import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
)
from app.db.deps import get_db
from app.models import Department, User, RefreshToken
from app.schemas.auth import RefreshIn, SignupIn, LoginIn, AuthOut

router = APIRouter(prefix="/api/auth", tags=["auth"])  # ✅ Changed from /auth to /api/auth

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@router.post("/signup")
async def signup(payload: SignupIn, db: AsyncSession = Depends(get_db)):
    # Check if user exists
    q = await db.execute(select(User).where(User.email == payload.email))
    existing = q.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Get or create default department
    dept_q = await db.execute(select(Department).where(Department.name == "General"))
    department = dept_q.scalar_one_or_none()
    
    if not department:
        # Create default department if it doesn't exist
        department = Department(name="General")
        db.add(department)
        await db.flush()  # Get department.id
    
    # Hash password
    hashed = hash_password(payload.password)
    
    # Create user WITH department_id
    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hashed,
        department_id=department.id  # ← ADD THIS LINE
    )
    
    db.add(user)
    await db.flush()  # get user.id
    await db.refresh(user)
    
    access = create_access_token(str(user.id))
    refresh_raw = create_refresh_token()

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.REFRESH_TOKEN_DAYS)

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256(refresh_raw),
            expires_at=expires,
            revoked_at=None,
        )
    )

    await db.commit()
    return AuthOut(access_token=access, refresh_token=refresh_raw)


@router.post("/login", response_model=AuthOut)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(User).where(User.email == str(payload.email).lower()))
    user = q.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    access = create_access_token(str(user.id))
    refresh_raw = create_refresh_token()

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.REFRESH_TOKEN_DAYS)

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256(refresh_raw),
            expires_at=expires,
            revoked_at=None,
        )
    )

    await db.commit()
    return AuthOut(access_token=access, refresh_token=refresh_raw)


@router.post("/refresh", response_model=AuthOut)
async def refresh(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    token_h = sha256(payload.refresh_token)

    now = datetime.now(timezone.utc)

    q = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.token_hash == token_h,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
    )
    rt = q.scalar_one_or_none()
    if not rt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # revoke old token (rotation)
    rt.revoked_at = now

    # issue new pair
    access = create_access_token(str(rt.user_id))
    new_refresh_raw = create_refresh_token()
    expires = now + timedelta(days=settings.REFRESH_TOKEN_DAYS)

    db.add(
        RefreshToken(
            user_id=rt.user_id,
            token_hash=sha256(new_refresh_raw),
            expires_at=expires,
            revoked_at=None,
        )
    )

    await db.commit()
    return AuthOut(access_token=access, refresh_token=new_refresh_raw)