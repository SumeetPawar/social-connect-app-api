"""
GET /api/coach  —  personal AI coaching report based on last 30 days.

Returns a structured coaching analysis:
  - summary     : 2-3 sentence coach intro
  - went_well   : list of wins with titles and details
  - improve     : list of gaps with specific actionable suggestions
  - focus       : the single most impactful next action
  - generated_at: when the report was generated
  - cached      : whether this was served from cache (< 7 days old)

Cached per user for 7 days. First call may take a few seconds (AI generation).
Subsequent calls within 7 days return instantly from DB.

Force refresh: GET /api/coach?refresh=true  (bypasses cache)
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.deps import get_db
from app.models import User
from app.services.ai_coach import get_coach_report

router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.get("")
async def coach(
    refresh: bool = Query(default=False, description="Force regenerate even if cached"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a personalised 30-day coaching report.

    Example response:
    {
      "summary": "You've been consistently active — 22 out of 30 days had steps logged...",
      "went_well": [
        {"title": "Strong step consistency", "body": "You logged steps on 22 of 30 days..."},
        {"title": "Habit momentum building", "body": "Your meditation habit hit 80%..."}
      ],
      "improve": [
        {
          "title": "Closing the step gap",
          "body": "Your daily average of 6,200 steps is 1,800 short of your 8,000 target.",
          "suggestion": "Add one 20-minute walk after lunch — that covers the gap."
        }
      ],
      "focus": "Hit your 8,000 step goal at least 5 days this week.",
      "generated_at": "2026-04-03T18:30:00+00:00",
      "cached": false
    }
    """
    if refresh:
        # Delete the latest cached report so get_coach_report regenerates
        from sqlalchemy import select, delete
        from app.models import AiCoachReport
        await db.execute(
            delete(AiCoachReport).where(AiCoachReport.user_id == str(user.id))
        )
        await db.commit()

    return await get_coach_report(db, str(user.id))
