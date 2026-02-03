from sqlalchemy import Boolean, Integer, Text, Date, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# ==========================================
# 1. ORGANIZATION & USERS
# ==========================================

class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('departments.id'), 
        nullable=True
    )
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Asia/Kolkata'"))
    department_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('departments.id'), 
        nullable=False
    )
    profile_pic_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Global streak tracking
    global_current_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    global_longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    global_perfect_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_streak_update: Mapped[str | None] = mapped_column(Date, nullable=True)
    
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    department_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('departments.id'), 
        nullable=False
    )
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    team_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id'), nullable=False)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'member'"))
    joined_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    left_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==========================================
# 2. AUTHENTICATION
# ==========================================

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==========================================
# 3. METRICS & TRACKING
# ==========================================

class GoalDefinition(Base):
    __tablename__ = "goal_definitions"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'int'"))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DailySteps(Base):
    __tablename__ = "daily_steps"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('users.id'), 
        primary_key=True
    )
    day: Mapped[str] = mapped_column(Date, primary_key=True)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DailyMetrics(Base):
    __tablename__ = "daily_metrics"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('users.id'), 
        primary_key=True
    )
    metric_key: Mapped[str] = mapped_column(
        Text, 
        ForeignKey('goal_definitions.key'), 
        primary_key=True
    )
    day: Mapped[str] = mapped_column(Date, primary_key=True)
    value_num: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BodyMetrics(Base):
    __tablename__ = "body_metrics"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    recorded_date: Mapped[str] = mapped_column(Date, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    muscle_mass_kg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    bmi: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ==========================================
# 4. CHALLENGES
# ==========================================

class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[str] = mapped_column(Date, nullable=False)
    end_date: Mapped[str] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    min_goals_required: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Flexible completion
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ChallengeDepartment(Base):
    __tablename__ = "challenge_departments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    challenge_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('challenges.id'), nullable=False)
    department_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('departments.id'), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ChallengeMetrics(Base):
    __tablename__ = "challenge_metrics"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    challenge_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('challenges.id'), nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, ForeignKey('goal_definitions.key'), nullable=False)
    target_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    rule_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'daily'"))


class ChallengeParticipant(Base):
    __tablename__ = "challenge_participants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    challenge_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('challenges.id'), nullable=False)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    team_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id'), nullable=True)
    joined_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    left_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_daily_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Challenge-specific streak tracking
    challenge_current_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    challenge_longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    challenge_perfect_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    challenge_total_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_activity_date: Mapped[str | None] = mapped_column(Date, nullable=True)


# ==========================================
# 5. GAMIFICATION
# ==========================================

class Badge(Base):
    __tablename__ = "badges"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BadgeAward(Base):
    __tablename__ = "badge_awards"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    badge_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('badges.id'), nullable=False)
    challenge_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), ForeignKey('challenges.id'), nullable=True)
    window_start: Mapped[str | None] = mapped_column(Date, nullable=True)
    window_end: Mapped[str | None] = mapped_column(Date, nullable=True)
    earned_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class UserBadgeProgress(Base):
    __tablename__ = "user_badge_progress"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('users.id'), 
        primary_key=True
    )
    badge_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey('badges.id'), 
        primary_key=True
    )
    times_earned: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    best_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_earned_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==========================================
# 6. TASKS
# ==========================================

class TaskDefinition(Base):
    __tablename__ = "task_definitions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str | None] = mapped_column(Text, ForeignKey('goal_definitions.key'), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TaskCompletion(Base):
    __tablename__ = "task_completions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    task_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('task_definitions.id'), nullable=False)
    day: Mapped[str] = mapped_column(Date, nullable=False)
    completed_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ==========================================
# 7. PERSONAL TASKS
# ==========================================

class UserTask(Base):
    __tablename__ = "user_tasks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    recurrence_type: Mapped[str] = mapped_column(Text, nullable=False)
    recurrence_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[str] = mapped_column(Date, nullable=False)
    end_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    next_due_date: Mapped[str] = mapped_column(Date, nullable=False)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    reminder_days_before: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    reminder_time: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'09:00:00'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserTaskCompletion(Base):
    __tablename__ = "user_task_completions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('user_tasks.id'), nullable=False)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    due_date: Mapped[str] = mapped_column(Date, nullable=False)
    completed_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ==========================================
# 8. AI INSIGHTS
# ==========================================

class AISummary(Base):
    __tablename__ = "ai_summaries"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[str] = mapped_column(Date, nullable=False)
    end_date: Mapped[str] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # ==========================================
# 9. PUSH NOTIFICATIONS
# ==========================================

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)