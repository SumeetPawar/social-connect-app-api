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
from app.models import User, RefreshToken
from app.schemas.auth import RefreshIn, SignupIn, LoginIn, AuthOut

router = APIRouter(prefix="/auth", tags=["auth"])


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@router.post("/signup", response_model=AuthOut)
async def signup(payload: SignupIn, db: AsyncSession = Depends(get_db)):
    # check existing
    q = await db.execute(select(User).where(User.email == payload.email))
    if q.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        name=payload.name,
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        is_email_verified=False,
        role="user",
        timezone="Asia/Kolkata",
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