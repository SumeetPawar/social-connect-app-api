from datetime import date, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.db.deps import get_db
from app.db.session import AsyncSessionLocal
from app.auth.deps import get_current_user
from app.models import Challenge, ChallengeDepartment, ChallengeMetrics, ChallengeParticipant, User
from app.services.challenges import ChallengesService
from app.schemas.challenges import (
    AvailableChallengeResponse,
    ChallengeCreateRequest,
    ChallengeMetricResponse,
    ChallengeUpdateRequest,
    ChallengeDetailResponse,
    ChallengeListResponse,
    JoinChallengeRequest,
    ParticipantResponse,
    ChallengeParticipantStatsResponse
)

router = APIRouter(prefix="/api/challenges", tags=["Challenges"])


@router.get("/available", response_model=List[AvailableChallengeResponse])
async def get_available_challenges(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all available challenges for the current user.
    Shows active challenges that the user can join.
    """
    today = date.today()
    
    # Subquery: Challenges in user's department
    dept_subquery = select(ChallengeDepartment.challenge_id).where(
        ChallengeDepartment.department_id == current_user.department_id
    )
    
    # Subquery: Company-wide challenges (no departments linked)
    company_wide_subquery = (
        select(Challenge.id)
        .outerjoin(ChallengeDepartment, Challenge.id == ChallengeDepartment.challenge_id)
        .where(ChallengeDepartment.id.is_(None))
    )
    
    # Main query
    stmt = select(Challenge).where(
        and_(
        Challenge.status == 'active',
            and_(
                Challenge.end_date >= today,
                or_(
                    Challenge.id.in_(dept_subquery),
                    Challenge.id.in_(company_wide_subquery)
                )
          )
        )
    ).order_by(Challenge.start_date)
    
    result = await db.execute(stmt)
    challenges = result.scalars().all()
    
    # Build response for each challenge
    response = []
    for challenge in challenges:
        # Get metrics
        metrics_stmt = select(ChallengeMetrics).where(
            ChallengeMetrics.challenge_id == challenge.id
        )
        metrics_result = await db.execute(metrics_stmt)
        metrics = metrics_result.scalars().all()
        
        # Get department IDs
        dept_stmt = select(ChallengeDepartment.department_id).where(
            ChallengeDepartment.challenge_id == challenge.id
        )
        dept_result = await db.execute(dept_stmt)
        department_ids = [str(dept_id) for dept_id in dept_result.scalars().all()]
        
        # Get participant count
        count_stmt = select(func.count(ChallengeParticipant.id)).where(
            and_(
                ChallengeParticipant.challenge_id == challenge.id,
                ChallengeParticipant.left_at.is_(None)
            )
        )
        count_result = await db.execute(count_stmt)
        participant_count = count_result.scalar() or 0
        
        # Check if user already joined
        part_stmt = select(ChallengeParticipant).where(
            and_(*[
                ChallengeParticipant.challenge_id == challenge.id,
                ChallengeParticipant.user_id == current_user.id,
                ChallengeParticipant.left_at.is_(None)
            ])
        )
        part_result = await db.execute(part_stmt)
        participant = part_result.scalar_one_or_none()
        
        response.append(AvailableChallengeResponse(
            id=str(challenge.id),
            title=challenge.title,
            description=challenge.description,  # ✅ add this
            period=challenge.period,
            scope=challenge.scope,
            start_date=challenge.start_date,
            end_date=challenge.end_date,
            status=challenge.status,
            min_goals_required=challenge.min_goals_required,
            created_by=str(challenge.created_by) if challenge.created_by else None,
            created_at=challenge.created_at,
            metrics=[
                ChallengeMetricResponse(
                    id=str(m.id),
                    challenge_id=str(m.challenge_id),
                    metric_key=m.metric_key,
                    target_value=m.target_value,
                    rule_type=m.rule_type
                ) for m in metrics
            ],
            department_ids=department_ids,
            participant_count=participant_count,
            user_joined=participant is not None,
            user_daily_target=participant.selected_daily_target if participant else None,
            days_remaining=(challenge.end_date - today).days + 1 if challenge.end_date >= today else 0
        ))
    
    return response

@router.post("", response_model=ChallengeDetailResponse, status_code=201)
async def create_challenge(
    challenge_data: ChallengeCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new challenge.
    
    - **title**: Challenge title (3-200 characters)
    - **period**: week or month
    - **scope**: individual, team, or department
    - **start_date**: Challenge start date
    - **end_date**: Challenge end date
    - **min_goals_required**: Minimum goals for daily success (null = all required)
    - **metrics**: List of metrics to track
    - **department_ids**: Departments for multi-dept challenge (null = company-wide)
    
    Requires authentication.
    """
    return await ChallengesService.create_challenge(
        db, 
        str(current_user.id), 
        challenge_data
    )

@router.get("", response_model=ChallengeListResponse)
async def list_challenges(
    status: Optional[str] = Query(None, description="Filter by status"),
    scope: Optional[str] = Query(None, description="Filter by scope"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List challenges with filters.
    
    - **status**: Filter by status (draft, active, completed, archived)
    - **scope**: Filter by scope (individual, team, department)
    - **page**: Page number (default: 1)
    - **page_size**: Results per page (default: 20, max: 100)
    
    Shows only challenges relevant to user's department.
    """
    return await ChallengesService.list_challenges(
        db,
        str(current_user.id),
        status,
        scope,
        page,
        page_size
    )

@router.get("/{challenge_id}/my-progress")
async def get_my_challenge_week_progress(
    challenge_id: UUID,
    start_date: date = Query(..., description="Week start date (Monday)"),
    end_date: date = Query(..., description="Week end date (Sunday)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's steps for a specific week within a specific challenge
    """
    
    # Get challenge info
    challenge_query = text("""
        SELECT id, title, start_date, end_date, status, period, scope
        FROM challenges
        WHERE id = :challenge_id
    """)
    
    result = await db.execute(challenge_query, {"challenge_id": challenge_id})
    challenge = result.mappings().first()
    
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    # Get user's daily target (from participation)
    participant_query = text("""
        SELECT selected_daily_target
        FROM challenge_participants
        WHERE challenge_id = :challenge_id 
        AND user_id = :user_id 
        AND left_at IS NULL
    """)
    
    result = await db.execute(
        participant_query, 
        {"challenge_id": challenge_id, "user_id": current_user.id}
    )
    participant = result.mappings().first()
    
    if not participant:
        raise HTTPException(
            status_code=403, 
            detail="You are not a participant in this challenge"
        )
    
    daily_target = participant['selected_daily_target'] or 5000
    
    # Get steps for the requested week
    steps_query = text("""
        SELECT day, steps as total_steps
        FROM daily_steps
        WHERE user_id = :user_id
        AND day >= :start_date
        AND day <= :end_date
        ORDER BY day
    """)
    
    result = await db.execute(
        steps_query,
        {
            "user_id": current_user.id,
            "start_date": start_date,
            "end_date": end_date
        }
    )
    steps_data = result.mappings().all()
    
    # Build 7-day array (Mon-Sun)
    days_array = []
    week_total = 0
    current_day = start_date
    
    while current_day <= end_date:
        day_data = next((d for d in steps_data if d['day'] == current_day), None)
        steps = day_data['total_steps'] if day_data else 0
        week_total += steps
        
        days_array.append({
            "day": str(current_day),
            "total_steps": steps
        })
        
        current_day += timedelta(days=1)
    
    return {
        "challenge_id": str(challenge['id']),
        "challenge_title": challenge['title'],
        "challenge_start": str(challenge['start_date']),
        "challenge_end": str(challenge['end_date']),
        "challenge_status": challenge['status'],
        "week_start": str(start_date),
        "week_end": str(end_date),
        "goal_daily_target": daily_target,
        "goal_period_target": daily_target * 7,
        "week_total_steps": week_total,
        "days": days_array
    }

@router.get("/my", response_model=List[ChallengeParticipantStatsResponse])
async def get_my_challenges(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get challenges I'm participating in with my stats.
    
    Returns list of active participations with streak and completion data.
    """
    return await ChallengesService.get_my_challenges(db, str(current_user.id))


@router.get("/{challenge_id}", response_model=ChallengeDetailResponse)
async def get_challenge(
    challenge_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get challenge details.
    
    Returns challenge information with metrics, departments, and participant count.
    """
    return await ChallengesService.get_challenge_detail(db, challenge_id)


@router.patch("/{challenge_id}", response_model=ChallengeDetailResponse)
async def update_challenge(
    challenge_id: str,
    update_data: ChallengeUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update challenge.
    
    Only challenge creator can update.
    """
    return await ChallengesService.update_challenge(
        db,
        challenge_id,
        str(current_user.id),
        update_data
    )


@router.post("/{challenge_id}/join", response_model=ParticipantResponse)
async def join_challenge(
    challenge_id: str,
    join_data: JoinChallengeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Join a challenge.
    
    - **team_id**: Team ID (required for team challenges)
    - **selected_daily_target**: Personal daily target for steps (3000/5000/7500/10000)
    """
    return await ChallengesService.join_challenge(
        db,
        challenge_id,
        str(current_user.id),
        join_data
    )


@router.post("/{challenge_id}/leave")
async def leave_challenge(
    challenge_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Leave a challenge.
    
    Sets left_at timestamp but preserves historical data.
    """
    return await ChallengesService.leave_challenge(
        db,
        challenge_id,
        str(current_user.id)
    )