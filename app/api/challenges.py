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
    
    # Get user's daily target AND streak info (READ ONLY - already saved)
    participant_query = text("""
        SELECT 
            selected_daily_target,
            challenge_current_streak,
            challenge_longest_streak,
            last_activity_date
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
    
    # ========== CALCULATE VALID DATE RANGE ==========
    challenge_start = challenge['start_date']
    challenge_end = challenge['end_date']
    
    actual_start = max(start_date, challenge_start)
    actual_end = min(end_date, challenge_end)
    
    valid_days_count = (actual_end - actual_start).days + 1 if actual_end >= actual_start else 0
    # ================================================
    
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
            "start_date": actual_start,
            "end_date": actual_end
        }
    )
    steps_data = result.mappings().all()
    
    # Build 7-day array (Mon-Sun)
    days_array = []
    week_total = 0
    current_day = start_date
    
    while current_day <= end_date:
        is_valid_day = (challenge_start <= current_day <= challenge_end)
        
        if is_valid_day:
            day_data = next((d for d in steps_data if d['day'] == current_day), None)
            steps = day_data['total_steps'] if day_data else 0
            week_total += steps
            
            days_array.append({
                "day": str(current_day),
                "total_steps": steps,
                "goal_met": steps >= daily_target,
                "is_challenge_day": True
            })
        else:
            days_array.append({
                "day": str(current_day),
                "total_steps": 0,
                "goal_met": False,
                "is_challenge_day": False
            })
        
        current_day += timedelta(days=1)
    
    # ========== JUST READ STREAK FROM DATABASE (Don't recalculate) ==========
    streak = {
        "current_streak": participant['challenge_current_streak'],
        "longest_streak": participant['challenge_longest_streak'],
        "last_activity_date": str(participant['last_activity_date']) if participant['last_activity_date'] else None
    }
    # ========================================================================
    
    return {
        "challenge_id": str(challenge['id']),
        "challenge_title": challenge['title'],
        "challenge_start": str(challenge['start_date']),
        "challenge_end": str(challenge['end_date']),
        "challenge_status": challenge['status'],
        "week_start": str(start_date),
        "week_end": str(end_date),
        "valid_days_in_week": valid_days_count,
        "goal_daily_target": daily_target,
        "goal_period_target": daily_target * valid_days_count,
        "week_total_steps": week_total,
        "days": days_array,
        "streak": streak
    }
    
@router.get("/{challenge_id}/leaderboard")
async def get_challenge_leaderboard(
    challenge_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get leaderboard for a specific challenge.
    Shows all participants ranked by total steps or completion percentage.
    """
    
    # Get challenge info
    challenge_query = text("""
        SELECT id, title, start_date, end_date, status
        FROM challenges
        WHERE id = :challenge_id
    """)
    
    result = await db.execute(challenge_query, {"challenge_id": challenge_id})
    challenge = result.mappings().first()
    
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    # Calculate days left
    from datetime import date
    today = date.today()
    days_left = (challenge['end_date'] - today).days if challenge['end_date'] >= today else 0
    
    # Calculate total days in challenge SO FAR (not full duration)
    start_date = challenge['start_date']
    end_date = challenge['end_date']
    challenge_end_or_today = min(end_date, today)
    total_challenge_days = (challenge_end_or_today - start_date).days + 1
    
    # Get total participants count
    participants_count_query = text("""
        SELECT COUNT(*) as total
        FROM challenge_participants
        WHERE challenge_id = :challenge_id
        AND left_at IS NULL
    """)
    
    result = await db.execute(participants_count_query, {"challenge_id": challenge_id})
    participants_count = result.scalar()
    
    # Get leaderboard data with completion percentage
    leaderboard_query = text("""
        WITH user_totals AS (
            SELECT 
                cp.user_id,
                u.name,
                cp.selected_daily_target,
                cp.challenge_current_streak,
                cp.challenge_longest_streak,
                COALESCE(SUM(ds.steps), 0) as total_steps,
                COALESCE(AVG(ds.steps), 0) as avg_steps,
                -- Count days where user met their goal
                COUNT(DISTINCT ds.day) FILTER (
                    WHERE ds.steps >= cp.selected_daily_target
                ) as days_met_goal,
                -- Count total days with ANY steps logged
                COUNT(DISTINCT ds.day) as days_logged
            FROM challenge_participants cp
            JOIN users u ON u.id = cp.user_id
            LEFT JOIN daily_steps ds ON ds.user_id = cp.user_id 
                AND ds.day >= :start_date 
                AND ds.day <= :end_date_or_today
            WHERE cp.challenge_id = :challenge_id
            AND cp.left_at IS NULL
            GROUP BY cp.user_id, u.name, cp.selected_daily_target, cp.challenge_current_streak, cp.challenge_longest_streak
        ),
        ranked AS (
            SELECT 
                user_id,
                name,
                selected_daily_target as goal,
                challenge_current_streak as streak,
                challenge_longest_streak as longest_streak,
                total_steps,
                avg_steps,
                days_met_goal,
                days_logged,
                -- Calculate completion percentage
                CASE 
                    WHEN :total_days > 0 THEN 
                        ROUND((days_met_goal::numeric / :total_days) * 100, 1)
                    ELSE 0 
                END as completion_pct,
                RANK() OVER (ORDER BY total_steps DESC) as rank
            FROM user_totals
        )
        SELECT 
            rank,
            user_id,
            name,
            goal,
            streak,
             longest_streak,
            total_steps,
            avg_steps,
            days_met_goal,
            days_logged,
            completion_pct
        FROM ranked
        ORDER BY rank
        LIMIT 100
    """)
    
    result = await db.execute(
        leaderboard_query,
        {
            "challenge_id": challenge_id,
            "start_date": start_date,
            "end_date_or_today": challenge_end_or_today,
            "total_days": total_challenge_days
        }
    )
    leaderboard_data = result.mappings().all()
    
    # Find current user in leaderboard
    my_rank = None
    my_total_steps = 0
    my_streak = 0
    my_longest_streak = 0
    my_daily_avg = 0
    my_goal = 0
    my_days_met_goal = 0
    my_completion_pct = 0
    
    for user in leaderboard_data:
        if str(user['user_id']) == str(current_user.id):
            my_rank = user['rank']
            my_total_steps = user['total_steps']
            my_streak = user['streak']
            my_longest_streak = max(user.get('longest_streak', 0) or 0, user['streak'])
            my_daily_avg = round(user['avg_steps'])
            my_goal = user['goal']
            my_days_met_goal = user['days_met_goal']
            my_completion_pct = user['completion_pct']
            break
    
    # Format leaderboard
    leaderboard = []
    for user in leaderboard_data:
        # Get initials
        name_parts = user['name'].split() if user['name'] else ['?', '?']
        initials = ''.join([part[0].upper() for part in name_parts[:2]])
        
        leaderboard.append({
            "rank": user['rank'],
            "user_id": str(user['user_id']),
            "name": user['name'],
            "initials": initials,
            "total_steps": user['total_steps'],
            "streak": user['streak'],
            "longest_streak": max(user.get('longest_streak', 0) or 0, user['streak']),
            "days_met_goal": user['days_met_goal'],
            "days_logged": user['days_logged'],
            "completion_pct": float(user['completion_pct']),
            "is_me": str(user['user_id']) == str(current_user.id),
            "is_top": user['rank'] == 1
        })
    
    # Determine badge based on rank
    badge = None
    if my_rank:
        if my_rank <= 3:
            badge = "Elite"
        elif my_rank <= 10:
            badge = "Pro"
        elif my_rank <= 25:
            badge = "Rising"
    
    # Calculate completion percentage (based on user's goal)
    challenge_completion_pct = 0
    if my_goal and my_goal > 0:
        total_days = (end_date - start_date).days + 1
        target_total = my_goal * total_days
        challenge_completion_pct = round((my_total_steps / target_total) * 100) if target_total > 0 else 0
    
    return {
        "challenge_id": str(challenge['id']),
        "challenge_title": challenge['title'],
        "challenge_goal": my_goal * ((end_date - start_date).days + 1) if my_goal else 200000,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "days_left": max(days_left, 0),
        "total_participants": participants_count,
        "completion_pct": challenge_completion_pct,
        "total_challenge_days": total_challenge_days,  # NEW
        "my_longest_streak": my_longest_streak,  # ADD THIS
        "my_rank": my_rank,
        "my_total_steps": my_total_steps,
        "my_streak": my_streak,
        "my_daily_avg": my_daily_avg,
        "my_badge": badge,
        "my_days_met_goal": my_days_met_goal,  # NEW
        "my_completion_pct": my_completion_pct,  # NEW
        
        "leaderboard": leaderboard
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