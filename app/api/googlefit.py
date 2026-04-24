from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.security import encrypt_token
from app.db.deps import get_db
from app.models import User, UserGoogleFitToken

router = APIRouter(prefix="/api/googlefit", tags=["Google Fit"])


class ConnectRequest(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until access_token expires


@router.get("/status")
async def google_fit_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns whether Google Fit is connected for the logged-in user.
    Frontend calls this on app load / settings screen.

    Response when connected:
      { "connected": true, "connected_since": "...", "last_synced": "..." }
    Response when not connected:
      { "connected": false }
    """
    result = await db.execute(
        select(UserGoogleFitToken).where(
            UserGoogleFitToken.user_id == str(current_user.id)
        )
    )
    token_row = result.scalar_one_or_none()

    if not token_row:
        return {"connected": False}

    return {
        "connected": True,
        "connected_since": token_row.created_at.isoformat(),
        "last_synced": token_row.updated_at.isoformat(),
    }


@router.post("/connect", status_code=200)
async def connect_google_fit(
    payload: ConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save (or overwrite) Google Fit OAuth tokens for the authenticated user.
    Tokens are encrypted at rest using a key derived from JWT_SECRET.
    """
    if payload.expires_in <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="expires_in must be a positive integer",
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=payload.expires_in)

    result = await db.execute(
        select(UserGoogleFitToken).where(
            UserGoogleFitToken.user_id == str(current_user.id)
        )
    )
    token_row = result.scalar_one_or_none()

    encrypted_access = encrypt_token(payload.access_token)
    encrypted_refresh = encrypt_token(payload.refresh_token)

    if token_row:
        token_row.access_token = encrypted_access
        token_row.refresh_token = encrypted_refresh
        token_row.expires_at = expires_at
        token_row.updated_at = datetime.now(timezone.utc)
    else:
        token_row = UserGoogleFitToken(
            user_id=str(current_user.id),
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            expires_at=expires_at,
        )
        db.add(token_row)

    await db.commit()
    return {"status": "connected"}


@router.delete("/disconnect", status_code=200)
async def disconnect_google_fit(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove stored Google Fit tokens for the authenticated user."""
    result = await db.execute(
        select(UserGoogleFitToken).where(
            UserGoogleFitToken.user_id == str(current_user.id)
        )
    )
    token_row = result.scalar_one_or_none()

    if token_row:
        await db.delete(token_row)
        await db.commit()

    return {"status": "disconnected"}
