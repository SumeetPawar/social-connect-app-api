"""
Google Fit daily step sync service.

Called by the scheduler every day at 08:00 IST.
For every user with a stored refresh_token:
  1. Refresh the access_token via Google OAuth.
  2. Fetch today's step count from the Fitness REST API.
  3. Upsert daily_steps (same logic as POST /api/steps/add).
  4. Persist the new access_token + expires_at.
  5. On token-revoked errors, delete the stored row.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta

import httpx
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
from app.db.session import AsyncSessionLocal
from app.models import DailySteps, UserGoogleFitToken

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_FIT_AGGREGATE_URL = (
    "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"
)


# ─── Token helpers ────────────────────────────────────────────────────────────

async def _refresh_access_token(
    client: httpx.AsyncClient,
    refresh_token: str,
) -> tuple[str, datetime]:
    """
    Exchange a refresh_token for a new access_token.
    Returns (access_token, expires_at).
    Raises httpx.HTTPStatusError on failure (caller should handle 400/401 as revoked).
    """
    resp = await client.post(
        _GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    expires_in = int(body.get("expires_in", 3600))
    access_token = body["access_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expires_at


# ─── Google Fit step fetch ────────────────────────────────────────────────────

async def _fetch_steps_for_date(
    client: httpx.AsyncClient,
    access_token: str,
    target_date: date,
) -> int:
    """
    Fetch total steps for a specific calendar day (IST) from Google Fit.
    Window: 00:00:00 IST on target_date → 00:00:00 IST on target_date+1
    Returns the sum of all step data points, or 0 if none.
    """
    _IST = timezone(timedelta(hours=5, minutes=30))
    start_dt = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=_IST)
    next_day  = target_date + timedelta(days=1)
    end_dt    = datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=_IST)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    resp = await client.post(
        _GOOGLE_FIT_AGGREGATE_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "aggregateBy": [{"dataTypeName": "com.google.step_count.delta"}],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": start_ms,
            "endTimeMillis": end_ms,
        },
        timeout=15,
    )
    resp.raise_for_status()

    total = 0
    for bucket in resp.json().get("bucket", []):
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                for val in point.get("value", []):
                    total += val.get("intVal", 0)
    return total


# ─── Steps upsert (mirrors POST /api/steps/add logic) ────────────────────────

async def _upsert_steps(db: AsyncSession, user_id: str, steps: int, target_date: date | None = None) -> None:
    """
    Insert or update daily_steps for the given date (defaults to today), then recalculate challenge streaks.
    Mirrors the core logic of the add_steps API endpoint.
    """
    from app.api.steps import calculate_challenge_streak  # local import to avoid circular deps

    today = target_date or date.today()

    result = await db.execute(
        select(DailySteps).where(
            and_(DailySteps.user_id == user_id, DailySteps.day == today)
        )
    )
    daily_steps = result.scalar_one_or_none()

    if daily_steps:
        if daily_steps.steps == steps:
            return  # nothing changed, skip streak recalc
        daily_steps.steps = steps
    else:
        daily_steps = DailySteps(user_id=user_id, day=today, steps=steps)
        db.add(daily_steps)

    # Snapshot ranks for active challenges covering today
    challenges_result = await db.execute(
        text("""
            SELECT DISTINCT c.id, c.start_date, LEAST(c.end_date, :today) AS end_cap
            FROM challenges c
            JOIN challenge_participants cp ON cp.challenge_id = c.id
            WHERE cp.user_id = :user_id
              AND cp.left_at IS NULL
              AND :today BETWEEN c.start_date AND c.end_date
        """),
        {"user_id": user_id, "today": today},
    )
    active_challenges = challenges_result.mappings().all()

    for ch in active_challenges:
        challenge_id = str(ch["id"])
        rank_row = await db.execute(
            text("""
                WITH totals AS (
                    SELECT cp.user_id,
                           COALESCE(SUM(ds.steps), 0) AS total_steps
                    FROM challenge_participants cp
                    LEFT JOIN daily_steps ds
                        ON ds.user_id = cp.user_id
                        AND ds.day >= :start AND ds.day <= :end_cap
                    WHERE cp.challenge_id = :cid AND cp.left_at IS NULL
                    GROUP BY cp.user_id
                )
                SELECT ROW_NUMBER() OVER (ORDER BY total_steps DESC) AS live_rank
                FROM totals WHERE user_id = :uid
            """),
            {
                "cid": challenge_id,
                "start": ch["start_date"],
                "end_cap": ch["end_cap"],
                "uid": user_id,
            },
        )
        rr = rank_row.mappings().first()
        if rr and rr["live_rank"]:
            await db.execute(
                text("""
                    UPDATE challenge_participants
                    SET previous_rank = :rank
                    WHERE challenge_id = :cid AND user_id = :uid
                """),
                {"rank": int(rr["live_rank"]), "cid": challenge_id, "uid": user_id},
            )

    await db.commit()
    await db.refresh(daily_steps)

    for ch in active_challenges:
        await calculate_challenge_streak(
            user_id=user_id,
            challenge_id=str(ch["id"]),
            db=db,
        )


# ─── Main sync entry-point ────────────────────────────────────────────────────

async def sync_all_users() -> None:
    """
    Scheduled job: refresh tokens + fetch + upsert steps for every connected user.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        logger.warning(
            "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not configured — skipping Google Fit sync"
        )
        return

    async with AsyncSessionLocal() as db:
        rows_result = await db.execute(select(UserGoogleFitToken))
        token_rows: list[UserGoogleFitToken] = list(rows_result.scalars().all())

    logger.info(f"Google Fit sync: processing {len(token_rows)} connected user(s)")

    async with httpx.AsyncClient() as client:
        for row in token_rows:
            user_id = str(row.user_id)
            try:
                # 1. Decrypt stored refresh_token
                refresh_token = decrypt_token(row.refresh_token)

                # 2. Refresh access_token
                new_access_token, new_expires_at = await _refresh_access_token(
                    client, refresh_token
                )

                # 3. Determine which dates to sync.
                # Morning run (08:05 IST) also back-fills yesterday to catch
                # any steps walked after the previous night's 23:05 sync.
                now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
                today = date.today()
                dates_to_sync = [today]
                if now_ist.hour < 10:  # morning run (08:05) → also sync yesterday
                    dates_to_sync.insert(0, today - timedelta(days=1))

                for sync_date in dates_to_sync:
                    steps = await _fetch_steps_for_date(client, new_access_token, sync_date)
                    logger.info(f"Google Fit sync: user={user_id} date={sync_date} steps={steps}")
                    if steps > 0:
                        async with AsyncSessionLocal() as db:
                            await _upsert_steps(db, user_id, steps, target_date=sync_date)

                # 5. Persist updated access_token + expires_at
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(UserGoogleFitToken).where(
                            UserGoogleFitToken.user_id == user_id
                        )
                    )
                    stored = result.scalar_one_or_none()
                    if stored:
                        stored.access_token = encrypt_token(new_access_token)
                        stored.expires_at = new_expires_at
                        stored.updated_at = datetime.now(timezone.utc)
                        await db.commit()

            except httpx.HTTPStatusError as exc:
                # 400/401 from Google → token revoked; clean up
                if exc.response.status_code in (400, 401):
                    logger.warning(
                        f"Google Fit sync: token revoked for user={user_id}, removing stored tokens"
                    )
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            select(UserGoogleFitToken).where(
                                UserGoogleFitToken.user_id == user_id
                            )
                        )
                        stored = result.scalar_one_or_none()
                        if stored:
                            await db.delete(stored)
                            await db.commit()
                else:
                    logger.error(
                        f"Google Fit sync: HTTP error for user={user_id}: {exc}",
                        exc_info=True,
                    )
            except Exception as exc:
                logger.error(
                    f"Google Fit sync: unexpected error for user={user_id}: {exc}",
                    exc_info=True,
                )
