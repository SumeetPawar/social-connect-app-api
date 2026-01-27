from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status
from typing import List, Optional
from datetime import date, datetime

from app.models import (
    Challenge,
    ChallengeMetrics,
    ChallengeDepartment,
    ChallengeParticipant,
    User,
    Team,
    Department
)
from app.schemas.challenges import (
    ChallengeCreateRequest,
    ChallengeUpdateRequest,
    ChallengeResponse,
    ChallengeDetailResponse,
    ChallengeMetricResponse,
    JoinChallengeRequest,
    ParticipantResponse,
    ChallengeParticipantStatsResponse
)


class ChallengesService:
    
    @staticmethod
    async def create_challenge(
        db: AsyncSession,
        user_id: str,
        challenge_data: ChallengeCreateRequest
    ) -> ChallengeDetailResponse:
        """Create a new challenge"""
        
        # Validate department IDs if provided
        if challenge_data.department_ids:
            dept_stmt = select(Department).where(
                Department.id.in_(challenge_data.department_ids)
            )
            dept_result = await db.execute(dept_stmt)
            departments = dept_result.scalars().all()
            
            if len(departments) != len(challenge_data.department_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more department IDs are invalid"
                )
        
        # Create challenge
        new_challenge = Challenge(
            title=challenge_data.title,
            period=challenge_data.period.value,
            scope=challenge_data.scope.value,
            start_date=challenge_data.start_date,
            end_date=challenge_data.end_date,
            status="draft",
            min_goals_required=challenge_data.min_goals_required,
            created_by=user_id
        )
        
        db.add(new_challenge)
        await db.flush()
        
        # Add metrics
        for metric in challenge_data.metrics:
            challenge_metric = ChallengeMetrics(
                challenge_id=str(new_challenge.id),
                metric_key=metric.metric_key,
                target_value=metric.target_value,
                rule_type=metric.rule_type.value
            )
            db.add(challenge_metric)
        
        # Add departments if specified
        if challenge_data.department_ids:
            for dept_id in challenge_data.department_ids:
                challenge_dept = ChallengeDepartment(
                    challenge_id=str(new_challenge.id),
                    department_id=dept_id
                )
                db.add(challenge_dept)
        
        await db.commit()
        await db.refresh(new_challenge)
        
        return await ChallengesService.get_challenge_detail(db, str(new_challenge.id))
    
    @staticmethod
    async def get_challenge_detail(
        db: AsyncSession,
        challenge_id: str
    ) -> ChallengeDetailResponse:
        """Get challenge details with metrics and departments"""
        
        # Get challenge
        challenge_stmt = select(Challenge).where(Challenge.id == challenge_id)
        challenge_result = await db.execute(challenge_stmt)
        challenge = challenge_result.scalar_one_or_none()
        
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Challenge not found"
            )
        
        # Get metrics
        metrics_stmt = select(ChallengeMetrics).where(
            ChallengeMetrics.challenge_id == challenge_id
        )
        metrics_result = await db.execute(metrics_stmt)
        metrics = metrics_result.scalars().all()
        
        # Get departments
        dept_stmt = select(ChallengeDepartment.department_id).where(
            ChallengeDepartment.challenge_id == challenge_id
        )
        dept_result = await db.execute(dept_stmt)
        department_ids = [str(dept_id) for dept_id in dept_result.scalars().all()]
        
        # Get participant count
        participant_stmt = select(func.count(ChallengeParticipant.id)).where(
            and_(
                ChallengeParticipant.challenge_id == challenge_id,
                ChallengeParticipant.left_at.is_(None)
            )
        )
        participant_result = await db.execute(participant_stmt)
        participant_count = participant_result.scalar() or 0
        
        return ChallengeDetailResponse(
            id=str(challenge.id),
            title=challenge.title,
            period=challenge.period,
            scope=challenge.scope,
            start_date=challenge.start_date,
            end_date=challenge.end_date,
            status=challenge.status,
            min_goals_required=challenge.min_goals_required,
            created_by=str(challenge.created_by) if challenge.created_by else None,
            created_at=challenge.created_at,
            metrics=[ChallengeMetricResponse.model_validate(m) for m in metrics],
            department_ids=department_ids,
            participant_count=participant_count
        )
    
    @staticmethod
    async def list_challenges(
        db: AsyncSession,
        user_id: str,
        status_filter: Optional[str] = None,
        scope_filter: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> dict:
        """List challenges with filters"""
        
        # Get user's department
        user_stmt = select(User).where(User.id == user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one()
        
        # Build query
        query = select(Challenge)
        
        # Status filter
        if status_filter:
            query = query.where(Challenge.status == status_filter)
        
        # Scope filter
        if scope_filter:
            query = query.where(Challenge.scope == scope_filter)
        
        # Department filter - show challenges that:
        # 1. Have no departments (company-wide)
        # 2. Include user's department
        dept_subquery = select(ChallengeDepartment.challenge_id).where(
            ChallengeDepartment.department_id == user.department_id
        )
        
        # Challenges with no departments (company-wide)
        no_dept_subquery = select(Challenge.id).outerjoin(
            ChallengeDepartment,
            Challenge.id == ChallengeDepartment.challenge_id
        ).where(ChallengeDepartment.id.is_(None))
        
        query = query.where(
            or_(
                Challenge.id.in_(dept_subquery),
                Challenge.id.in_(no_dept_subquery)
            )
        )
        
        # Order by created_at desc
        query = query.order_by(desc(Challenge.created_at))
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0
        
        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        result = await db.execute(query)
        challenges = result.scalars().all()
        
        return {
            "challenges": [ChallengeResponse.model_validate(c) for c in challenges],
            "total": total,
            "page": page,
            "page_size": page_size
        }
    
    @staticmethod
    async def update_challenge(
        db: AsyncSession,
        challenge_id: str,
        user_id: str,
        update_data: ChallengeUpdateRequest
    ) -> ChallengeDetailResponse:
        """Update challenge"""
        
        # Get challenge
        stmt = select(Challenge).where(Challenge.id == challenge_id)
        result = await db.execute(stmt)
        challenge = result.scalar_one_or_none()
        
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Challenge not found"
            )
        
        # Check permission
        if str(challenge.created_by) != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this challenge"
            )
        
        # Update fields
        if update_data.title:
            challenge.title = update_data.title
        if update_data.status:
            challenge.status = update_data.status.value
        if update_data.min_goals_required is not None:
            challenge.min_goals_required = update_data.min_goals_required
        
        await db.commit()
        
        return await ChallengesService.get_challenge_detail(db, challenge_id)
    
    @staticmethod
    async def join_challenge(
        db: AsyncSession,
        challenge_id: str,
        user_id: str,
        join_data: JoinChallengeRequest
    ) -> ParticipantResponse:
        """Join a challenge"""
        
        # Get challenge
        challenge_stmt = select(Challenge).where(Challenge.id == challenge_id)
        challenge_result = await db.execute(challenge_stmt)
        challenge = challenge_result.scalar_one_or_none()
        
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Challenge not found"
            )
        
        # Check if already joined
        existing_stmt = select(ChallengeParticipant).where(
            and_(
                ChallengeParticipant.challenge_id == challenge_id,
                ChallengeParticipant.user_id == user_id,
                ChallengeParticipant.left_at.is_(None)
            )
        )
        existing_result = await db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Already joined this challenge"
            )
        
        # Validate team if provided
        if join_data.team_id:
            team_stmt = select(Team).where(Team.id == join_data.team_id)
            team_result = await db.execute(team_stmt)
            team = team_result.scalar_one_or_none()
            
            if not team:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Team not found"
                )
        
        # Create participant
        participant = ChallengeParticipant(
            challenge_id=challenge_id,
            user_id=user_id,
            team_id=join_data.team_id,
            selected_daily_target=join_data.selected_daily_target,
            challenge_current_streak=0,
            challenge_longest_streak=0,
            challenge_perfect_days=0,
            challenge_total_score=0
        )
        
        db.add(participant)
        await db.commit()
        await db.refresh(participant)
        
        # Get user and team info
        user_stmt = select(User).where(User.id == user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one()
        
        team_name = None
        if join_data.team_id:
            team_stmt = select(Team.name).where(Team.id == join_data.team_id)
            team_result = await db.execute(team_stmt)
            team_name = team_result.scalar_one_or_none()
        
        return ParticipantResponse(
            id=str(participant.id),
            challenge_id=challenge_id,
            user_id=user_id,
            user_name=user.name,
            team_id=join_data.team_id,
            team_name=team_name,
            joined_at=participant.joined_at,
            selected_daily_target=participant.selected_daily_target,
            challenge_current_streak=participant.challenge_current_streak,
            challenge_longest_streak=participant.challenge_longest_streak,
            challenge_perfect_days=participant.challenge_perfect_days,
            challenge_total_score=participant.challenge_total_score
        )
    
    @staticmethod
    async def leave_challenge(
        db: AsyncSession,
        challenge_id: str,
        user_id: str
    ) -> dict:
        """Leave a challenge"""
        
        stmt = select(ChallengeParticipant).where(
            and_(
                ChallengeParticipant.challenge_id == challenge_id,
                ChallengeParticipant.user_id == user_id,
                ChallengeParticipant.left_at.is_(None)
            )
        )
        result = await db.execute(stmt)
        participant = result.scalar_one_or_none()
        
        if not participant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not a participant of this challenge"
            )
        
        participant.left_at = datetime.utcnow()
        await db.commit()
        
        return {"message": "Left challenge successfully"}
    
    @staticmethod
    async def get_my_challenges(
        db: AsyncSession,
        user_id: str
    ) -> List[ChallengeParticipantStatsResponse]:
        """Get challenges user is participating in"""
        
        stmt = select(ChallengeParticipant, Challenge).join(
            Challenge,
            ChallengeParticipant.challenge_id == Challenge.id
        ).where(
            and_(
                ChallengeParticipant.user_id == user_id,
                ChallengeParticipant.left_at.is_(None)
            )
        ).order_by(desc(ChallengeParticipant.joined_at))
        
        result = await db.execute(stmt)
        records = result.all()
        
        stats_list = []
        for participant, challenge in records:
            total_days = (challenge.end_date - challenge.start_date).days + 1
            completion_pct = (participant.challenge_perfect_days / total_days * 100) if total_days > 0 else 0
            
            stats_list.append(
                ChallengeParticipantStatsResponse(
                    challenge_id=str(challenge.id),
                    challenge_title=challenge.title,
                    user_id=user_id,
                    joined_at=participant.joined_at,
                    selected_daily_target=participant.selected_daily_target,
                    current_streak=participant.challenge_current_streak,
                    longest_streak=participant.challenge_longest_streak,
                    perfect_days=participant.challenge_perfect_days,
                    total_score=participant.challenge_total_score,
                    days_completed=participant.challenge_perfect_days,
                    total_days=total_days,
                    completion_percentage=round(completion_pct, 2)
                )
            )
        
        return stats_list