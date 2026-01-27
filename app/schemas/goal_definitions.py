from pydantic import BaseModel
from datetime import datetime

class GoalDefinitionResponse(BaseModel):
    key: str
    label: str
    description: str
    unit: str
    value_type: str
    created_at: datetime
    
    class Config:
        from_attributes = True