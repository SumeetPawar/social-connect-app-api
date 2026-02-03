from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime
from typing import Optional, List
from enum import Enum


# ==========================================
# ENUMS
# ==========================================

class ChallengePeriod(str, Enum):
    WEEK = "week"
    MONTH = "month"


class ChallengeScope(str, Enum):
    INDIVIDUAL = "individual"
    TEAM = "team"
    DEPARTMENT = "department"


class ChallengeStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class MetricRuleType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


# ==========================================
# METRIC SCHEMAS
# ==========================================

class ChallengeMetricRequest(BaseModel):
    """Challenge metric configuration"""
    metric_key: str = Field(..., description="Goal key (e.g., 'steps', 'water')")
    target_value: Optional[float] = Field(None, description="Target value (null for user-selectable)")
    rule_type: MetricRuleType = Field(default=MetricRuleType.DAILY)


class ChallengeMetricResponse(BaseModel):
    """Challenge metric response"""
    id: str
    challenge_id: str
    metric_key: str
    target_value: Optional[float]
    rule_type: str
    
    class Config:
        from_attributes = True


# ==========================================
# REQUEST SCHEMAS
# ==========================================

class ChallengeCreateRequest(BaseModel):
    """Create challenge request"""
    title: str = Field(..., min_length=3, max_length=200)
    period: ChallengePeriod
    scope: ChallengeScope
    start_date: date
    end_date: date
    
    # Optional flexible completion rule
    min_goals_required: Optional[int] = Field(
        None,
        ge=1,
        description="Minimum goals required for daily success (null = all required)"
    )
    
    # Metrics to track
    metrics: List[ChallengeMetricRequest] = Field(..., min_length=1)
    
    # Multi-department support
    department_ids: Optional[List[str]] = Field(
        None,
        description="Department IDs for multi-dept challenges (null = company-wide)"
    )
    
    @field_validator('end_date')
    @classmethod
    def validate_dates(cls, v, info):
        if 'start_date' in info.data and v < info.data['start_date']:
            raise ValueError('end_date must be after start_date')
        return v
    
    @field_validator('metrics')
    @classmethod
    def validate_metrics(cls, v):
        if len(v) == 0:
            raise ValueError('At least one metric is required')
        
        # Check for duplicate metrics
        metric_keys = [m.metric_key for m in v]
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError('Duplicate metrics not allowed')
        
        return v
    
    @field_validator('min_goals_required')
    @classmethod
    def validate_min_goals(cls, v, info):
        if v is not None and 'metrics' in info.data:
            if v > len(info.data['metrics']):
                raise ValueError('min_goals_required cannot exceed total metrics count')
        return v


class ChallengeUpdateRequest(BaseModel):
    """Update challenge request"""
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    status: Optional[ChallengeStatus] = None
    min_goals_required: Optional[int] = Field(None, ge=1)


class JoinChallengeRequest(BaseModel):
    """Join challenge request"""
    team_id: Optional[str] = Field(None, description="Team ID (for team challenges)")
    selected_daily_target: Optional[int] = Field(
        None,
        description="Personal daily target (3000/5000/7500/10000 for step challenges)"
    )
    
    @field_validator('selected_daily_target')
    @classmethod
    def validate_target(cls, v):
        if v is not None and v not in [3000, 5000, 7500, 10000]:
            raise ValueError('Daily target must be 3000, 5000, 7500, or 10000')
        return v


# ==========================================
# RESPONSE SCHEMAS
# ==========================================

class ChallengeResponse(BaseModel):
    """Challenge response"""
    id: str
    title: str
    period: str
    scope: str
    start_date: date
    end_date: date
    status: str
    min_goals_required: Optional[int]
    created_by: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class ChallengeDetailResponse(BaseModel):
    """Detailed challenge response with metrics"""
    id: str
    title: str
    period: str
    scope: str
    start_date: date
    end_date: date
    status: str
    min_goals_required: Optional[int]
    created_by: Optional[str]
    created_at: datetime
    
    # Related data
    metrics: List[ChallengeMetricResponse]
    department_ids: List[str]
    participant_count: int
    
    class Config:
        from_attributes = True


class ParticipantResponse(BaseModel):
    """Challenge participant response"""
    id: str
    challenge_id: str
    user_id: str
    user_name: Optional[str]
    team_id: Optional[str]
    team_name: Optional[str]
    joined_at: datetime
    selected_daily_target: Optional[int]
    
    # Streak info
    challenge_current_streak: int
    challenge_longest_streak: int
    challenge_perfect_days: int
    challenge_total_score: int
    
    class Config:
        from_attributes = True


class ChallengeParticipantStatsResponse(BaseModel):
    """My participation stats in a challenge"""
    challenge_id: str
    challenge_title: str
    user_id: str
    joined_at: datetime
    selected_daily_target: Optional[int]
    
    # Streak stats
    current_streak: int
    longest_streak: int
    perfect_days: int
    total_score: int
    
    # Progress
    days_completed: int
    total_days: int
    completion_percentage: float


class ChallengeListResponse(BaseModel):
    """List of challenges with pagination"""
    challenges: List[ChallengeResponse]
    total: int
    page: int
    page_size: int
    


class AvailableChallengeResponse(BaseModel):
    """Available challenge with user participation info"""
    id: str
    title: str
    description: Optional[str]
    period: str
    scope: str
    start_date: date
    end_date: date
    status: str
    min_goals_required: Optional[int]
    created_by: Optional[str]
    created_at: datetime
    
    # Related data
    metrics: List[ChallengeMetricResponse]
    department_ids: List[str]
    participant_count: int
    
    # User-specific data
    user_joined: bool
    user_daily_target: Optional[int]
    days_remaining: int