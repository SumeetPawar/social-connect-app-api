from sqlalchemy import Column, Text, DateTime
from sqlalchemy.sql import func
from app.db.base import Base  # your Base

class GoalDefinition(Base):
    __tablename__ = "goal_definitions"

    key = Column(Text, primary_key=True)   # 'steps', 'water'
    label = Column(Text, nullable=False)
    unit = Column(Text, nullable=False)
    value_type = Column(Text, nullable=False, default="int")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
