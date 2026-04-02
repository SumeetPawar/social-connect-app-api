"""
Script to manually create a Steps Challenge for the CURRENT month, per department.
- Skips any department that already has a challenge for this month (no duplicates).
- Enrolls all users in each department with their most recent daily target (default 5000).

Usage:
    python scripts/create_current_month_challenge.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import calendar
from datetime import date
from sqlalchemy import insert, select, text

from app.db.session import AsyncSessionLocal
from app.models import Challenge, ChallengeDepartment, ChallengeParticipant, User
from app.models import Department


async def create_current_month_challenges():
    today = date.today()
    year = today.year
    month = today.month

    start_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = date(year, month, last_day)

    month_label = start_date.strftime("%B %Y")
    challenge_title = f"{start_date.strftime('%B')} Steps Challenge"

    print(f"\n[create_current_month_challenge] Month: {month_label}")
    print(f"  Period: {start_date} to {end_date}")

    async with AsyncSessionLocal() as db:
        # Get all departments
        result = await db.execute(select(Department))
        departments = result.scalars().all()

        if not departments:
            print("  No departments found. Exiting.")
            return

        total_created = 0
        total_skipped = 0
        total_enrolled = 0

        for dept in departments:
            print(f"\n  Department: {dept.name} ({dept.id})")

            # --- Duplicate check ---
            dup_result = await db.execute(text("""
                SELECT c.id, c.title
                FROM challenges c
                JOIN challenge_departments cd ON cd.challenge_id = c.id
                WHERE cd.department_id = :dept_id
                  AND c.start_date = :start_date
                  AND c.end_date   = :end_date
                LIMIT 1
            """), {
                "dept_id": str(dept.id),
                "start_date": start_date,
                "end_date": end_date,
            })
            existing = dup_result.mappings().first()

            if existing:
                print(f"    [SKIP] Already exists: \"{existing['title']}\" (id={existing['id']})")
                total_skipped += 1
                continue

            # --- Create challenge ---
            ins_result = await db.execute(
                insert(Challenge).values(
                    title=challenge_title,
                    description=f"Monthly step challenge for {dept.name} department.",
                    period="month",
                    scope="department",
                    start_date=start_date,
                    end_date=end_date,
                    status="active",
                ).returning(Challenge.id)
            )
            challenge_id = ins_result.scalar_one()
            print(f"    [OK] Created challenge id={challenge_id}")

            # --- Link to department ---
            await db.execute(
                insert(ChallengeDepartment).values(
                    challenge_id=challenge_id,
                    department_id=dept.id,
                )
            )

            # --- Enroll all users in the department ---
            users_result = await db.execute(
                select(User).where(User.department_id == dept.id)
            )
            users = users_result.scalars().all()

            enrolled_count = 0
            for user in users:
                # Get user's most recent daily target from any active challenge
                target_result = await db.execute(text("""
                    SELECT cp.selected_daily_target
                    FROM challenge_participants cp
                    JOIN challenges c ON c.id = cp.challenge_id
                    WHERE cp.user_id = :user_id
                      AND cp.left_at IS NULL
                    ORDER BY c.start_date DESC
                    LIMIT 1
                """), {"user_id": str(user.id)})
                row = target_result.mappings().first()
                daily_target = int(row["selected_daily_target"]) if row and row["selected_daily_target"] else 5000

                await db.execute(
                    insert(ChallengeParticipant).values(
                        challenge_id=challenge_id,
                        user_id=user.id,
                        selected_daily_target=daily_target,
                    )
                )
                enrolled_count += 1
                total_enrolled += 1

            print(f"    [OK] Enrolled {enrolled_count} users (department: {dept.name})")
            total_created += 1

        await db.commit()

        print(f"\n[Done]")
        print(f"  Challenges created : {total_created}")
        print(f"  Departments skipped: {total_skipped} (already have a challenge this month)")
        print(f"  Users enrolled     : {total_enrolled}")

        # --- Summary: show all departments and their current-month challenge ---
        print(f"{'-'*80}")
        print(f"  {'DEPARTMENT':<25} {'CHALLENGE NAME':<30} {'STATUS':<10} ID")
        print(f"{'-'*80}")
        for dept in departments:
            summary_result = await db.execute(text("""
                SELECT c.id, c.title, c.status
                FROM challenges c
                JOIN challenge_departments cd ON cd.challenge_id = c.id
                WHERE cd.department_id = :dept_id
                  AND c.start_date = :start_date
                  AND c.end_date   = :end_date
                LIMIT 1
            """), {
                "dept_id": str(dept.id),
                "start_date": start_date,
                "end_date": end_date,
            })
            row = summary_result.mappings().first()
            if row:
                print(f"  {dept.name:<25} {row['title']:<30} {row['status']:<10} {row['id']}")
            else:
                print(f"  {dept.name:<25} {'(no challenge this month)':<30}")
        print(f"{'-'*80}")


if __name__ == "__main__":
    asyncio.run(create_current_month_challenges())
