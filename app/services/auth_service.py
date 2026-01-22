from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
)
from app.models.refresh_token import RefreshToken


def rotate_refresh_token(db: Session, raw_refresh_token: str):
    now = datetime.now(timezone.utc)
    token_hash = hash_refresh_token(raw_refresh_token)

    rt: RefreshToken | None = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )

    # Not found -> invalid
    if not rt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Revoked -> invalid
    if rt.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked",
        )

    # Expired -> invalid
    if rt.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    # ✅ Rotate: revoke old
    rt.revoked_at = now

    # ✅ Issue new refresh token (raw for client, hash for DB)
    new_raw = create_refresh_token()
    new_hash = hash_refresh_token(new_raw)

    # Choose your refresh TTL setting (example: days)
    # Add settings.REFRESH_TOKEN_DAYS = 30 (or whatever you already use)
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_DAYS)

    new_rt = RefreshToken(
        user_id=rt.user_id,
        token_hash=new_hash,
        expires_at=expires_at,
    )

    db.add(new_rt)
    db.commit()

    # ✅ New access JWT
    access = create_access_token(str(rt.user_id))

    return access, new_raw
