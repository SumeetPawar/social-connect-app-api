import uuid
from sqlalchemy import Column, Date, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.base import Base

class Goal(Base):
    __tablename__ = "goals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    metric_key = Column(Text, ForeignKey("goal_definitions.key"), nullable=False)
    period = Column(Text, nullable=False)  # 'week' | 'month'

    daily_target = Column(Numeric(12, 2), nullable=False)
    period_target = Column(Numeric(12, 2), nullable=False)

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    anchor_start = Column(Date, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
