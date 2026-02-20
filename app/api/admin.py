# backend/routes/admin.py

from typing import List
from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.security import hash_password
import logging
from app.db.deps import get_db
from app.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"])

class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    is_email_verified: bool
    created_at: str
    
    class Config:
        from_attributes = True


class AdminResetPasswordRequest(BaseModel):
    user_id: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    message: str
    user_email: str
    

# ============================================
# HELPER FUNCTION
# ============================================

def require_admin(current_user: User):
    """Check if current user is admin"""
    if current_user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )


# ============================================
# ENDPOINTS
# ============================================

@router.get("/users", response_model=List[UserResponse])
async def get_all_users(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of all users (admin only)
    """
    require_admin(current_user)
    
    # Use async select instead of query
    result = await db.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    
    return [
        UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            is_email_verified=user.is_email_verified,
            created_at=user.created_at.isoformat()
        )
        for user in users
    ]


@router.post("/users/reset-password", response_model=ResetPasswordResponse)
async def admin_reset_user_password(
    request: AdminResetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Reset password for any user (admin only)
    """
    logging.info(f"[ADMIN] {current_user.email} (ID: {current_user.id}) is attempting to reset password for user_id={request.user_id}")
    require_admin(current_user)
    
    # Validate password length
    if len(request.new_password) < 7:
        logging.warning(f"[ADMIN] {current_user.email} tried to set a too-short password for user_id={request.user_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 7 characters"
        )
    
    # Find target user using async select
    result = await db.execute(
        select(User).where(User.id == request.user_id)
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        logging.error(f"[ADMIN] {current_user.email} tried to reset password for non-existent user_id={request.user_id}")
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from resetting other admin passwords (optional security)
    # if target_user.role == 'admin' and str(target_user.id) != str(current_user.id):
    #     raise HTTPException(status_code=403, detail="Cannot reset other admin passwords")
    
    # Update password
    logging.info(f"[ADMIN] {current_user.email} is resetting password for {target_user.email} (ID: {target_user.id})")
    target_user.password_hash = hash_password(request.new_password)
    await db.commit()
    logging.info(f"[ADMIN] Password reset successful for {target_user.email} (ID: {target_user.id}) by {current_user.email}")
    
    # TODO: Send email notification later
    # send_password_changed_notification(target_user.email)
    
    return ResetPasswordResponse(
        message="Password reset successfully",
        user_email=target_user.email
    )