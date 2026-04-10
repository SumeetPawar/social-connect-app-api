"""
app/api/body_metrics.py

Body composition scan tracking — save scans, fetch latest, fetch history.
All periods returned in a single /history call.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import date, timedelta
from typing import Optional
from pydantic import BaseModel

from app.db.deps import get_db
from app.auth.deps import get_current_user
from app.models import User, BodyMetrics, AiRecommendation
from app.schemas.body_metrics import BodyMetricCreate, BodyMetricOut, HistoryResponse, HistoryResponse          # adjust import path if needed
from app.services.ai_recommendations import get_body_insight
from sqlalchemy import delete

router = APIRouter(prefix="/api/body-metrics", tags=["Body Metrics"])
 
# ─── Helpers ─────────────────────────────────────────────────────────────────

def _calc_bmi(weight_kg: float, height_cm: float) -> Optional[float]:
    if not weight_kg or not height_cm:
        return None
    h = height_cm / 100
    return round(float(weight_kg) / float(h * h), 1)


@router.post("")
async def save_scan(
    data: BodyMetricCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save a new body composition scan.
    Returns the saved record + a fresh AI insight (cached insight is invalidated on every save).
    """

    # Use override height or fall back to user's stored height
    height = data.height_cm or getattr(current_user, "height_cm", None)
    bmi = _calc_bmi(data.weight_kg, height) if data.weight_kg else None

    recorded_date = data.recorded_date or date.today()
    
    # Check if record for same date already exists
    stmt = select(BodyMetrics).where(
        and_(
            BodyMetrics.user_id == current_user.id,
            BodyMetrics.recorded_date == recorded_date
        )
    )
    result = await db.execute(stmt)
    existing_record = result.scalar_one_or_none()
    
    if existing_record:
        # Update existing record
        existing_record.weight_kg = data.weight_kg
        existing_record.bmi = bmi
        existing_record.body_fat_pct = data.body_fat_pct
        existing_record.subcutaneous_fat_pct = data.subcutaneous_fat_pct
        existing_record.visceral_fat = data.visceral_fat
        existing_record.muscle_mass_kg = data.muscle_mass_kg
        existing_record.bone_mass_kg = data.bone_mass_kg
        existing_record.hydration_pct = data.hydration_pct
        existing_record.protein_pct = data.protein_pct
        existing_record.bmr_kcal = data.bmr_kcal
        existing_record.metabolic_age = data.metabolic_age
        existing_record.skeletal_muscle_pct = data.skeletal_muscle_pct
        record = existing_record
    else:
        # Create new record
        record = BodyMetrics(
            user_id        = current_user.id,
            recorded_date  = recorded_date,
            weight_kg      = data.weight_kg,
            bmi            = bmi,
            body_fat_pct   = data.body_fat_pct,
            subcutaneous_fat_pct = data.subcutaneous_fat_pct,
            visceral_fat   = data.visceral_fat,
            muscle_mass_kg = data.muscle_mass_kg,
            bone_mass_kg   = data.bone_mass_kg,
            hydration_pct  = data.hydration_pct,
            protein_pct    = data.protein_pct,
            bmr_kcal       = data.bmr_kcal,
            metabolic_age  = data.metabolic_age,
            skeletal_muscle_pct = data.skeletal_muscle_pct
        )
        db.add(record)
    
    await db.commit()
    await db.refresh(record)

    # Invalidate cached body insight — new scan data means stale analysis
    await db.execute(
        delete(AiRecommendation).where(
            AiRecommendation.user_id == str(current_user.id),
            AiRecommendation.type == "body_insight",
        )
    )
    await db.commit()

    # Generate fresh AI insight (runs synchronously — typically <3s)
    ai_insight = await get_body_insight(db, str(current_user.id))

    return {
        "scan":       BodyMetricOut.model_validate(record),
        "ai_insight": ai_insight,
    }

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


@router.get("/insight")
async def body_ai_insight(
    refresh: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AI analysis of body composition trends across all logged scans.

    Returns:
      - trend_summary : overall 2-3 sentence picture
      - highlights    : [{metric, direction, note}] — key metrics with plain-English notes
      - warning       : most important thing to watch (null if nothing concerning)
      - tip           : one specific, actionable lifestyle recommendation

    Cached 7 days. Use ?refresh=true to force regeneration.
    Returns 404 if no body scans have been logged yet.
    """
    if refresh:
        from sqlalchemy import delete
        from app.models import AiRecommendation
        await db.execute(
            delete(AiRecommendation).where(
                AiRecommendation.user_id == str(current_user.id),
                AiRecommendation.type == "body_insight",
            )
        )
        await db.commit()

    result = await get_body_insight(db, str(current_user.id))
    if result is None:
        raise HTTPException(404, "No body scans found — log at least one measurement first.")
    return result