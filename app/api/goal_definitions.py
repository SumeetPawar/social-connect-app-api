from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db.deps import get_db
from app.models import GoalDefinition
from app.schemas.goal_definitions import GoalDefinitionResponse

router = APIRouter(prefix="/goal-definitions", tags=["goal-definitions"])


@router.get("", response_model=List[GoalDefinitionResponse])
async def list_goal_definitions(
    db: AsyncSession = Depends(get_db),
):
    """
    Get all available goal definitions with their descriptions.
    Use this to show users what goals they can track.
    """
    stmt = select(GoalDefinition).order_by(GoalDefinition.key)
    result = await db.execute(stmt)
    goals = result.scalars().all()
    
    return goals