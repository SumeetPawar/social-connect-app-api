from datetime import date
from typing import Optional
from pydantic import BaseModel, field_validator
from uuid import UUID


class BodyMetricCreate(BaseModel):
    recorded_date:  Optional[date]  = None   # defaults to today
    weight_kg:      Optional[float] = None
    body_fat_pct:   Optional[float] = None
    visceral_fat:   Optional[float] = None
    muscle_mass_kg: Optional[float] = None
    bone_mass_kg:   Optional[float] = None
    hydration_pct:  Optional[float] = None
    protein_pct:    Optional[float] = None
    bmr_kcal:       Optional[int]   = None
    metabolic_age:  Optional[int]   = None
    height_cm:      Optional[float] = None   # override user's stored height


class BodyMetricOut(BaseModel):
    id: str
    user_id: str
    recorded_date: date
    weight_kg: Optional[float]
    bmi: Optional[float]
    body_fat_pct: Optional[float]
    visceral_fat: Optional[float]
    muscle_mass_kg: Optional[float]
    bone_mass_kg: Optional[float]
    hydration_pct: Optional[float]
    protein_pct: Optional[float]
    bmr_kcal: Optional[int]
    metabolic_age: Optional[int]

    class Config:
        from_attributes = True

    @field_validator('id', 'user_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        if isinstance(v, UUID):
            return str(v)
        return v

class HistoryResponse(BaseModel):
    """All periods in one response — frontend picks what it needs."""
    all:  list[BodyMetricOut]
    y1:   list[BodyMetricOut]   # last 12 months
    m6:   list[BodyMetricOut]   # last 6 months
    m3:   list[BodyMetricOut]   # last 3 months

