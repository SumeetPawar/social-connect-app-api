import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from passlib.context import CryptContext
from jose import JWTError, jwt

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    # bcrypt only supports passwords up to 72 bytes
    # Truncate password to 72 bytes for bcrypt
    password_bytes = password.encode('utf-8')[:72]
    return pwd_context.hash(password_bytes)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.ACCESS_TOKEN_MIN)
    payload = {"sub": user_id, "type": "access", "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def create_refresh_token() -> str:
    # raw token stored client-side; we store only hash in DB
    return secrets.token_urlsafe(48)

def decode_token(
    token: str,
    expected_type: str | None = None,  # "access" or "refresh" (if you ever make refresh JWT)
    secret_key: str | None = None,
    algorithm: str | None = None,
):
    """
    Decodes a JWT and returns its payload.
    If expected_type is provided, validates payload["type"] == expected_type.
    Raises HTTPException(401) on any error.
    """
    secret_key = secret_key or settings.JWT_SECRET
    algorithm = algorithm or settings.JWT_ALG

    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])

        if expected_type is not None:
            if payload.get("type") != expected_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                )

        if not payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        return payload

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
        
def decode_access_token(token: str):
    return decode_token(token, expected_type="access")

def hash_refresh_token(raw_token: str) -> str:
    # Pepper the token hash so DB leak alone isn't enough to brute/guess
    data = f"{raw_token}:{settings.JWT_SECRET}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()