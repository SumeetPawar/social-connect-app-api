
from pydantic import BaseModel
from typing import Optional

class ProfileUpdate(BaseModel):
    age:            Optional[int]   = None   # years
    gender:         Optional[str]   = None   # "male" | "female"
    activity_level: Optional[str]   = None   # "sedentary"|"light"|"moderate"|"active"|"athlete"
    height_cm:      Optional[float] = None

class ProfileOut(BaseModel):
    age:            Optional[int]
    gender:         Optional[str]
    activity_level: Optional[str]
    height_cm:      Optional[float]

    class Config:
        from_attributes = True