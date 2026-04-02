from datetime import date, datetime
import enum

from sqlalchemy import Boolean, Integer, String, String, Text, Date, DateTime, Numeric, ForeignKey, Enum as SAEnum, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # Body composition profile — for personalised ideal ranges
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)          # "male" | "female"
    activity_level: Mapped[str | None] = mapped_column(Text, nullable=True)  # "sedentary"|"light"|"moderate"|"active"|"athlete"
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
        
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    habit_challenges: Mapped[list["HabitChallenge"]] = relationship(back_populates="user")


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
    subcutaneous_fat_pct: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    muscle_mass_kg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    bmi: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Extended scan fields (added migration 0005)
    visceral_fat: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    bone_mass_kg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    hydration_pct: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    protein_pct: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    bmr_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metabolic_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skeletal_muscle_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

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
    # Stores the previous day's leaderboard rank for rank shift tracking
    previous_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stores the previous day's consistency leaderboard rank
    previous_consistency_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


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

    # ── Add these to your existing models.py ─────────────────────────────────────
# Assumes you already have: User, Base, mapped_column, Mapped, relationship etc.
    # ==========================================
# 10. Habits, Challenges, and Gamification models
# ==========================================
 


class ChallengeStatus(str, enum.Enum):
    active    = "active"
    completed = "completed"
    abandoned = "abandoned"


class HabitCategory(str, enum.Enum):
    Body      = "Body"
    Mind      = "Mind"
    Lifestyle = "Lifestyle"


class HabitTier(str, enum.Enum):
    core   = "core"
    growth = "growth"
    avoid  = "avoid"


class Habit(Base):
    __tablename__ = "habits"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True)
    slug:        Mapped[str]           = mapped_column(String(64), unique=True, nullable=False)
    label:       Mapped[str]           = mapped_column(String(255), nullable=False)
    description: Mapped[str]           = mapped_column(String(512), nullable=False)
    why:         Mapped[str]           = mapped_column(Text, nullable=False)
    impact:      Mapped[str]           = mapped_column(String(64), nullable=False)
    category:    Mapped[HabitCategory] = mapped_column(SAEnum(HabitCategory), nullable=False)
    tier:        Mapped[HabitTier]     = mapped_column(SAEnum(HabitTier), nullable=False)
    has_counter: Mapped[bool]          = mapped_column(Boolean, default=False)
    unit:        Mapped[str | None]    = mapped_column(String(32), nullable=True)
    target:      Mapped[int | None]    = mapped_column(Integer, nullable=True)

    commitments: Mapped[list["HabitCommitment"]] = relationship(back_populates="habit")


class HabitChallenge(Base):
    __tablename__ = "habit_challenges"

    id:         Mapped[int]             = mapped_column(Integer, primary_key=True)
    user_id:    Mapped[str]             = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    pack_id:    Mapped[str | None]      = mapped_column(String(64), nullable=True)
    status:     Mapped[ChallengeStatus] = mapped_column(SAEnum(ChallengeStatus), default=ChallengeStatus.active)
    started_at: Mapped[date]            = mapped_column(Date, nullable=False)
    ends_at:    Mapped[date]            = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())

    user:        Mapped["User"]                  = relationship(back_populates="habit_challenges")
    commitments: Mapped[list["HabitCommitment"]] = relationship(back_populates="challenge", cascade="all, delete-orphan")


class HabitCommitment(Base):
    __tablename__ = "habit_commitments"
    __table_args__ = (UniqueConstraint("challenge_id", "habit_id"),)

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(Integer, ForeignKey("habit_challenges.id", ondelete="CASCADE"))
    habit_id:     Mapped[int] = mapped_column(Integer, ForeignKey("habits.id"))
    sort_order:   Mapped[int] = mapped_column(Integer, default=0)

    challenge: Mapped["HabitChallenge"]  = relationship(back_populates="commitments")
    habit:     Mapped["Habit"]          = relationship(back_populates="commitments")
    logs:      Mapped[list["DailyLog"]] = relationship(back_populates="commitment", cascade="all, delete-orphan")


class DailyLog(Base):
    __tablename__ = "daily_logs"
    __table_args__ = (UniqueConstraint("commitment_id", "logged_date"),)

    id:            Mapped[int]        = mapped_column(Integer, primary_key=True)
    commitment_id: Mapped[int]        = mapped_column(Integer, ForeignKey("habit_commitments.id", ondelete="CASCADE"))
    logged_date:   Mapped[date]       = mapped_column(Date, nullable=False)
    completed:     Mapped[bool]       = mapped_column(Boolean, default=False)
    value:         Mapped[int | None] = mapped_column(Integer, nullable=True)
    logged_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())

    commitment: Mapped["HabitCommitment"] = relationship(back_populates="logs")


# Also add to your User model:
# challenges: Mapped[list["Challenge"]] = relationship(back_populates="user")


class DailyPushCount(Base):
    """Tracks how many push notifications a user has received on a given day.
    Used for the per-user daily cap. Stored in the DB so it survives restarts
    and is shared across worker processes."""
    __tablename__ = "daily_push_counts"

    user_id:   Mapped[str]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    push_date: Mapped[date] = mapped_column(Date, nullable=False, primary_key=True)
    count:     Mapped[int]  = mapped_column(Integer, nullable=False, default=0)