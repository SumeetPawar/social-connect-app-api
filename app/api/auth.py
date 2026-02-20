import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
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


# Import allowed emails list from separate file
from app.core.allowed_signup_emails import ALLOWED_SIGNUP_EMAILS

router = APIRouter(prefix="/api/auth", tags=["auth"])  # ✅ Changed from /auth to /api/auth

logger = logging.getLogger(__name__)

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()



@router.post("/signup")
async def signup(payload: SignupIn, db: AsyncSession = Depends(get_db)):
    # Restrict signup to allowed emails
    email_lc = str(payload.email).lower()
    if email_lc not in [e.lower() for e in ALLOWED_SIGNUP_EMAILS]:
        raise HTTPException(status_code=403, detail="Signup not allowed for this email")

    # Check if user exists
    q = await db.execute(select(User).where(func.lower(User.email) == email_lc))
    existing = q.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Get or create default department
    dept_q = await db.execute(select(Department).where(Department.name == "GESBMS"))
    department = dept_q.scalar_one_or_none()

    if not department:
        # Create default department if it doesn't exist
        department = Department(name="GESBMS")
        db.add(department)
        await db.flush()  # Get department.id

    # Hash password
    hashed = hash_password(payload.password)

    # Create user WITH department_id, email lowercased
    user = User(
        name=payload.name,
        email=email_lc,
        password_hash=hashed,
        department_id=department.id
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

    email_lc = str(payload.email).lower()
    logger.info(f"Login: Looking up user for email: {email_lc}")
    q = await db.execute(select(User).where(func.lower(User.email) == email_lc))
    user = q.scalar_one_or_none()
    logger.info(f"Login: User found: {bool(user)}")


    # DEBUG: Log password, hash, and verification result (remove sensitive info in production)
    if user:
        logger.info(f"Login: Attempt for user: {user.email}")
        logger.info(f"Login: Provided password: {payload.password}")
        logger.info(f"Login: Stored hash: {user.password_hash}")
        try:
            result = verify_password(payload.password, user.password_hash)
        except Exception as e:
            logger.error(f"Login: Exception in verify_password: {e}")
            result = False
        logger.info(f"Login: verify_password result: {result}")
    else:
        logger.info(f"Login: No user found for email: {payload.email}")

    if not user or not verify_password(payload.password, user.password_hash):
        logger.warning(f"Login: Authentication failed for email: {payload.email}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    logger.info(f"Login: Authentication successful for user: {user.email}")
    access = create_access_token(str(user.id))
    logger.info(f"Login: Access token created for user: {user.email}")
    refresh_raw = create_refresh_token()
    logger.info(f"Login: Refresh token created for user: {user.email}")

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
    logger.info(f"Login: RefreshToken added to DB for user: {user.email}")

    await db.commit()
    logger.info(f"Login: DB commit complete for user: {user.email}")
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