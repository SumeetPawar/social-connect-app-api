# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select, func, and_, desc, or_
# from app.models import DailySteps
# from app.models import User
# from app.models import ChallengeParticipant
# from app.schemas.steps import (
#     StepsLogRequest, 
#     StepsResponse, 
#     StepsStatsResponse,
#     StepsWithStreakResponse,
#     DailyStepsSummary
# )
# from datetime import date, timedelta
# from typing import Optional, List
# from uuid import UUID


# class StepsService:
    
#     @staticmethod
#     async def log_steps(
#         db: AsyncSession,
#         user_id: UUID,
#         steps_data: StepsLogRequest
#     ) -> StepsWithStreakResponse:
#         """Log or update steps for a specific day"""
        
#         # Check if entry exists
#         stmt = select(DailySteps).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day == steps_data.day
#             )
#         )
#         result = await db.execute(stmt)
#         daily_steps = result.scalar_one_or_none()
        
#         if daily_steps:
#             # Update existing
#             daily_steps.steps = steps_data.steps
#             daily_steps.updated_at = func.now()
#         else:
#             # Create new
#             daily_steps = DailySteps(
#                 user_id=user_id,
#                 day=steps_data.day,
#                 steps=steps_data.steps
#             )
#             db.add(daily_steps)
        
#         await db.commit()
#         await db.refresh(daily_steps)
        
#         # Get user streak info
#         user_stmt = select(User).where(User.id == user_id)
#         user_result = await db.execute(user_stmt)
#         user = user_result.scalar_one()
        
#         # Check if target met (we'll get this from active challenges)
#         target_met = False
#         daily_target = None
        
#         # Get user's active challenge targets
#         challenge_stmt = select(ChallengeParticipant).where(
#             and_(
#                 ChallengeParticipant.user_id == user_id,
#                 ChallengeParticipant.left_at.is_(None),
#                 ChallengeParticipant.selected_daily_target.isnot(None)
#             )
#         )
#         challenge_result = await db.execute(challenge_stmt)
#         challenges = challenge_result.scalars().all()
        
#         if challenges:
#             # Use the highest target
#             daily_target = max(c.selected_daily_target for c in challenges)
#             target_met = steps_data.steps >= daily_target
        
#         return StepsWithStreakResponse(
#             user_id=str(user_id),
#             day=daily_steps.day,
#             steps=daily_steps.steps,
#             updated_at=daily_steps.updated_at,
#             current_streak=user.global_current_streak,
#             longest_streak=user.global_longest_streak,
#             target_met=target_met,
#             daily_target=daily_target
#         )
    
#     @staticmethod
#     async def get_steps_by_day(
#         db: AsyncSession,
#         user_id: UUID,
#         day: date
#     ) -> Optional[StepsResponse]:
#         """Get steps for a specific day"""
        
#         stmt = select(DailySteps).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day == day
#             )
#         )
#         result = await db.execute(stmt)
#         daily_steps = result.scalar_one_or_none()
        
#         if not daily_steps:
#             return None
        
#         return StepsResponse(
#             user_id=str(daily_steps.user_id),
#             day=daily_steps.day,
#             steps=daily_steps.steps,
#             updated_at=daily_steps.updated_at
#         )
    
#     @staticmethod
#     async def get_steps_history(
#         db: AsyncSession,
#         user_id: UUID,
#         start_date: date,
#         end_date: date
#     ) -> List[StepsResponse]:
#         """Get steps history for a date range"""
        
#         stmt = select(DailySteps).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day >= start_date,
#                 DailySteps.day <= end_date
#             )
#         ).order_by(desc(DailySteps.day))
        
#         result = await db.execute(stmt)
#         steps_records = result.scalars().all()
        
#         return [
#             StepsResponse(
#                 user_id=str(record.user_id),
#                 day=record.day,
#                 steps=record.steps,
#                 updated_at=record.updated_at
#             )
#             for record in steps_records
#         ]
    
#     @staticmethod
#     async def get_steps_stats(
#         db: AsyncSession,
#         user_id: UUID,
#         start_date: date,
#         end_date: date
#     ) -> StepsStatsResponse:
#         """Get statistics for a date range"""
        
#         # Query stats
#         stmt = select(
#             func.sum(DailySteps.steps).label('total'),
#             func.avg(DailySteps.steps).label('average'),
#             func.count(DailySteps.day).label('days_logged'),
#             func.count(DailySteps.day).filter(DailySteps.steps > 0).label('days_with_steps'),
#             func.max(DailySteps.steps).label('max_steps')
#         ).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day >= start_date,
#                 DailySteps.day <= end_date
#             )
#         )
        
#         result = await db.execute(stmt)
#         stats = result.first()
        
#         # Get day with highest steps
#         highest_day = None
#         highest_steps = None
        
#         if stats.max_steps:
#             highest_stmt = select(DailySteps.day, DailySteps.steps).where(
#                 and_(
#                     DailySteps.user_id == user_id,
#                     DailySteps.steps == stats.max_steps,
#                     DailySteps.day >= start_date,
#                     DailySteps.day <= end_date
#                 )
#             ).limit(1)
#             highest_result = await db.execute(highest_stmt)
#             highest_record = highest_result.first()
#             if highest_record:
#                 highest_day = highest_record.day
#                 highest_steps = highest_record.steps
        
#         # Get user streak info
#         user_stmt = select(User).where(User.id == user_id)
#         user_result = await db.execute(user_stmt)
#         user = user_result.scalar_one()
        
#         return StepsStatsResponse(
#             total_steps=stats.total or 0,
#             average_steps=float(stats.average or 0),
#             days_logged=stats.days_logged or 0,
#             days_with_steps=stats.days_with_steps or 0,
#             highest_day=highest_day,
#             highest_steps=highest_steps,
#             current_streak=user.global_current_streak,
#             longest_streak=user.global_longest_streak
#         )
    
#     @staticmethod
#     async def get_weekly_summary(
#         db: AsyncSession,
#         user_id: UUID,
#         week_start: date
#     ) -> List[DailyStepsSummary]:
#         """Get 7-day summary starting from week_start"""
        
#         week_end = week_start + timedelta(days=6)
        
#         # Get all steps for the week
#         stmt = select(DailySteps).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day >= week_start,
#                 DailySteps.day <= week_end
#             )
#         ).order_by(DailySteps.day)
        
#         result = await db.execute(stmt)
#         steps_records = {record.day: record for record in result.scalars().all()}
        
#         # Get user's daily target
#         target = None
#         challenge_stmt = select(ChallengeParticipant.selected_daily_target).where(
#             and_(
#                 ChallengeParticipant.user_id == user_id,
#                 ChallengeParticipant.left_at.is_(None),
#                 ChallengeParticipant.selected_daily_target.isnot(None)
#             )
#         )
#         challenge_result = await db.execute(challenge_stmt)
#         targets = challenge_result.scalars().all()
#         if targets:
#             target = max(targets)
        
#         # Build 7-day summary
#         summary = []
#         current_day = week_start
        
#         for i in range(7):
#             record = steps_records.get(current_day)
#             steps = record.steps if record else 0
#             target_met = steps >= target if target else False
#             percentage = (steps / target * 100) if target and target > 0 else 0
            
#             summary.append(
#                 DailyStepsSummary(
#                     day=current_day,
#                     steps=steps,
#                     target=target,
#                     target_met=target_met,
#                     percentage=min(percentage, 999)  # Cap at 999%
#                 )
#             )
#             current_day += timedelta(days=1)
        
#         return summary
    
#     @staticmethod
#     async def delete_steps(
#         db: AsyncSession,
#         user_id: UUID,
#         day: date
#     ) -> bool:
#         """Delete steps for a specific day"""
        
#         stmt = select(DailySteps).where(
#             and_(
#                 DailySteps.user_id == user_id,
#                 DailySteps.day == day
#             )
#         )
#         result = await db.execute(stmt)
#         daily_steps = result.scalar_one_or_none()
        
#         if not daily_steps:
#             return False
        
#         await db.delete(daily_steps)
#         await db.commit()
        
#         return True