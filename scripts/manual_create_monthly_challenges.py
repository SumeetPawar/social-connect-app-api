"""
Manual test script to trigger department-wise monthly challenge creation and enrollment.
Usage: python scripts/manual_create_monthly_challenges.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from datetime import datetime
from app.db.session import AsyncSessionLocal
from app.services.reminder_service import create_next_monthly_challenge_and_enroll_users


def main():
    print("[Manual Test] Creating department-wise monthly challenges...")
    async def run():
        async with AsyncSessionLocal() as db:
            await create_next_monthly_challenge_and_enroll_users(db)
    asyncio.run(run())
    print(f"[Manual Test] Done at {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
