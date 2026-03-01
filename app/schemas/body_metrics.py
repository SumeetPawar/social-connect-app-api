from datetime import date
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict
 


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
    id:             UUID        # ← was int
    user_id:        UUID        # ← was int
    recorded_date:  date
    weight_kg:      Optional[float] = None
    bmi:            Optional[float] = None
    body_fat_pct:   Optional[float] = None
    visceral_fat:   Optional[float] = None
    muscle_mass_kg: Optional[float] = None
    bone_mass_kg:   Optional[float] = None
    hydration_pct:  Optional[float] = None
    protein_pct:    Optional[float] = None
    bmr_kcal:       Optional[int]   = None
    metabolic_age:  Optional[int]   = None

    model_config = ConfigDict(from_attributes=True)

class HistoryResponse(BaseModel):
    """All periods in one response — frontend picks what it needs."""
    all:  list[BodyMetricOut]
    y1:   list[BodyMetricOut]   # last 12 months
    m6:   list[BodyMetricOut]   # last 6 months
    m3:   list[BodyMetricOut]   # last 3 months

