"""
Regenerate AI body-composition insights for every user who has logged body metrics.

Steps per user:
  1. Delete any cached `body_insight` row from ai_recommendations.
  2. Call get_body_insight() → triggers a fresh AI call and saves the new result.

Usage (from the project root):
  .venv311\Scripts\python.exe scripts\refresh_body_metrics_ai.py
  python scripts/refresh_body_metrics_ai.py --user-id <UUID>
  python scripts/refresh_body_metrics_ai.py --dry-run
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import BodyMetrics, AiRecommendation, User
from app.services.ai_recommendations import get_body_insight


# ── helpers ───────────────────────────────────────────────────────────────────

async def get_users_with_body_metrics(db: AsyncSession) -> list[str]:
    """Return distinct user_ids that have at least one body_metrics row."""
    result = await db.execute(
        select(BodyMetrics.user_id).distinct()
    )
    return [str(row[0]) for row in result.all()]


async def clear_cached_insight(db: AsyncSession, user_id: str) -> int:
    """Delete all cached body_insight rows for a user. Returns number deleted."""
    result = await db.execute(
        delete(AiRecommendation)
        .where(
            AiRecommendation.user_id == user_id,
            AiRecommendation.type == "body_insight",
        )
        .returning(AiRecommendation.id)
    )
    await db.commit()
    return len(result.all())


async def get_user_email(db: AsyncSession, user_id: str) -> str:
    result = await db.execute(select(User.email).where(User.id == user_id))
    row = result.scalar_one_or_none()
    return row or user_id


# ── main logic ────────────────────────────────────────────────────────────────

async def refresh_for_user(db: AsyncSession, user_id: str, dry_run: bool) -> dict:
    email = await get_user_email(db, user_id)

    if dry_run:
        print(f"  [DRY-RUN] Would regenerate body insight for {email}")
        return {"user_id": user_id, "email": email, "status": "dry-run"}

    # 1. Clear cache
    deleted = await clear_cached_insight(db, user_id)
    print(f"  Cleared {deleted} cached insight(s) for {email}")

    # 2. Regenerate
    try:
        result = await get_body_insight(db, user_id)
        if result is None:
            print(f"  ⚠  No body data returned for {email} (insufficient metrics?)")
            return {"user_id": user_id, "email": email, "status": "no_data"}
        cached_flag = result.get("cached", False)
        print(f"  ✓  Insight generated for {email}  (cached={cached_flag})")
        return {"user_id": user_id, "email": email, "status": "ok", "cached": cached_flag}
    except Exception as exc:
        print(f"  ✗  Error for {email}: {exc}")
        return {"user_id": user_id, "email": email, "status": "error", "error": str(exc)}


async def main(target_user_id: str | None, dry_run: bool):
    print(f"\n=== Body Metrics AI Refresh  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    if dry_run:
        print("  Mode: DRY-RUN (no changes will be made)\n")

    async with AsyncSessionLocal() as db:
        if target_user_id:
            user_ids = [target_user_id]
            print(f"Target: single user {target_user_id}\n")
        else:
            user_ids = await get_users_with_body_metrics(db)
            print(f"Found {len(user_ids)} user(s) with body metrics logs\n")

        if not user_ids:
            print("Nothing to do.")
            return

        results = []
        for i, uid in enumerate(user_ids, 1):
            print(f"[{i}/{len(user_ids)}] {uid}")
            res = await refresh_for_user(db, uid, dry_run)
            results.append(res)

    # ── summary ───────────────────────────────────────────────────────────────
    ok      = sum(1 for r in results if r["status"] == "ok")
    no_data = sum(1 for r in results if r["status"] == "no_data")
    errors  = sum(1 for r in results if r["status"] == "error")
    dry     = sum(1 for r in results if r["status"] == "dry-run")

    print(f"\n── Summary ──────────────────────────────────────────")
    print(f"  Total users  : {len(results)}")
    if dry_run:
        print(f"  Dry-run      : {dry}")
    else:
        print(f"  Regenerated  : {ok}")
        print(f"  No data      : {no_data}")
        print(f"  Errors       : {errors}")

    if errors:
        print("\nFailed users:")
        for r in results:
            if r["status"] == "error":
                print(f"  {r['email']} — {r.get('error', '')}")

    print()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Regenerate AI body-composition insights for users with logged body metrics"
    )
    parser.add_argument(
        "--user-id",
        metavar="UUID",
        help="Refresh a single user instead of all users",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List affected users without making any changes",
    )
    args = parser.parse_args()

    asyncio.run(main(target_user_id=args.user_id, dry_run=args.dry_run))
