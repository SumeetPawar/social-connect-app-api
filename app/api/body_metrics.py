"""
app/api/body_metrics.py

Body composition scan tracking — save scans, fetch latest, fetch history.
All periods returned in a single /history call.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, timedelta
from typing import Optional
from pydantic import BaseModel

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User, BodyMetrics
from app.schemas.body_metrics import BodyMetricCreate, BodyMetricOut, HistoryResponse, HistoryResponse          # adjust import path if needed

router = APIRouter(prefix="/api/body-metrics", tags=["Body Metrics"])
 
# ─── Helpers ─────────────────────────────────────────────────────────────────

def _calc_bmi(weight_kg: float, height_cm: float) -> Optional[float]:
    if not weight_kg or not height_cm:
        return None
    h = float(height_cm) / 100
    return round(float(weight_kg) / (h * h), 1)

# ─── Routes ─────────────────────────────────────────────────────────────────
@router.post("", response_model=BodyMetricOut)
async def save_scan(
    data: BodyMetricCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    height = data.height_cm or getattr(current_user, "height_cm", None)
    bmi = _calc_bmi(data.weight_kg, height) if data.weight_kg else None
    scan_date = data.recorded_date or date.today()

    # Check if a scan already exists for this user on this date
    stmt = select(BodyMetrics).where(
        BodyMetrics.user_id == current_user.id,
        BodyMetrics.recorded_date == scan_date,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing record
        existing.weight_kg      = data.weight_kg
        existing.bmi            = bmi
        existing.body_fat_pct   = data.body_fat_pct
        existing.visceral_fat   = data.visceral_fat
        existing.muscle_mass_kg = data.muscle_mass_kg
        existing.bone_mass_kg   = data.bone_mass_kg
        existing.hydration_pct  = data.hydration_pct
        existing.protein_pct    = data.protein_pct
        existing.bmr_kcal       = data.bmr_kcal
        existing.metabolic_age  = data.metabolic_age
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        # Insert new record
        record = BodyMetrics(
            user_id        = current_user.id,
            recorded_date  = scan_date,
            weight_kg      = data.weight_kg,
            bmi            = bmi,
            body_fat_pct   = data.body_fat_pct,
            visceral_fat   = data.visceral_fat,
            muscle_mass_kg = data.muscle_mass_kg,
            bone_mass_kg   = data.bone_mass_kg,
            hydration_pct  = data.hydration_pct,
            protein_pct    = data.protein_pct,
            bmr_kcal       = data.bmr_kcal,
            metabolic_age  = data.metabolic_age,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record

@router.get("/latest", response_model=BodyMetricOut)
async def get_latest(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent scan for the dashboard."""
    stmt = (
        select(BodyMetrics)
        .where(BodyMetrics.user_id == current_user.id)
        .order_by(BodyMetrics.recorded_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "No scans found — log your first measurement.")
    return record


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return ALL scan history in one call, pre-sliced into periods.
    Frontend uses: response.m3 / response.m6 / response.y1 / response.all
    No need for multiple API calls when switching chart period.
    """
    stmt = (
        select(BodyMetrics)
        .where(BodyMetrics.user_id == current_user.id)
        .order_by(BodyMetrics.recorded_date.asc())
    )
    result = await db.execute(stmt)
    all_records = result.scalars().all()

    today = date.today()
    cutoffs = {
        "y1": today - timedelta(days=365),
        "m6": today - timedelta(days=180),
        "m3": today - timedelta(days=90),
    }

    return HistoryResponse(
        all = all_records,
        y1  = [r for r in all_records if r.recorded_date >= cutoffs["y1"]],
        m6  = [r for r in all_records if r.recorded_date >= cutoffs["m6"]],
        m3  = [r for r in all_records if r.recorded_date >= cutoffs["m3"]],
    )