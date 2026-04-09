"""
Manually trigger any scheduled job without waiting for its cron time.

Jobs available
--------------
  ranks         — Snapshot leaderboard previous_rank for all active challenges
  streak        — Streak-at-risk push alerts (20:00 IST job)
  reminders     — Evening step-count push reminders (21:00 IST job)
  nudge         — Midday/afternoon challenge step nudges
  habit_morning — Habit morning reminder push (07:30 IST job)
  habit_evening — Habit evening nudge push (20:30 IST job)
  weekly        — Weekly habit/step summary push (Sunday 20:00 IST)
  insight       — Nightly AI insight generation for all users (00:30 IST job)
  habit_cycle   — Habit cycle completion summary push (21:00 IST job)
  rank_change   — Rank-change push notifications (21:30 IST job)
  monthly       — Monthly challenge auto-creation (last day of month 23:55)
  test_push     — Send one sample test push to the hard-coded test user
  all           — Run every job above in sequence

Usage examples (from project root)
------------------------------------
  python scripts/test_scheduler.py ranks
  python scripts/test_scheduler.py insight
  python scripts/test_scheduler.py all
  python scripts/test_scheduler.py habit_morning habit_evening weekly
"""

import asyncio
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── logging — show INFO so job output is visible ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
# Suppress SQLAlchemy query noise unless DEBUG is requested
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

logger = logging.getLogger("test_scheduler")

# ── ANSI ──────────────────────────────────────────────────────────────────────
_RST  = "\033[0m"
_BOLD = "\033[1m"
_DIM  = "\033[2m"
_GRN  = "\033[32m"
_RED  = "\033[31m"
_YEL  = "\033[33m"
_CYN  = "\033[36m"


def _hdr(name: str) -> None:
    bar = "─" * 60
    print(f"\n{_BOLD}{bar}{_RST}")
    print(f"  {_BOLD}{_CYN}▶ {name}{_RST}")
    print(f"{_BOLD}{bar}{_RST}")


def _ok(name: str, elapsed: float) -> None:
    print(f"  {_GRN}✔ {name} completed{_RST}  {_DIM}({elapsed:.2f}s){_RST}\n")


def _fail(name: str, err: str, elapsed: float) -> None:
    print(f"  {_RED}✘ {name} FAILED{_RST}  {_DIM}({elapsed:.2f}s){_RST}")
    print(f"  {_RED}{err}{_RST}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Individual job runners  (each opens its own DB session, matching real usage)
# ─────────────────────────────────────────────────────────────────────────────

async def run_ranks():
    from app.services.scheduler import update_all_previous_ranks
    await update_all_previous_ranks()


async def run_streak():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_streak_at_risk
    async with AsyncSessionLocal() as db:
        await send_streak_at_risk(db)


async def run_reminders():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_step_reminders
    async with AsyncSessionLocal() as db:
        await send_step_reminders(db)


async def run_nudge():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_challenge_step_nudges
    async with AsyncSessionLocal() as db:
        await send_challenge_step_nudges(db)


async def run_habit_morning():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_habit_morning_reminder
    async with AsyncSessionLocal() as db:
        await send_habit_morning_reminder(db)


async def run_habit_evening():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_habit_evening_nudge
    async with AsyncSessionLocal() as db:
        await send_habit_evening_nudge(db)


async def run_weekly():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_weekly_summary
    async with AsyncSessionLocal() as db:
        await send_weekly_summary(db)


async def run_insight(user_id: str | None = None):
    from app.db.session import AsyncSessionLocal
    from app.services.ai_insight import generate_nightly_insights
    async with AsyncSessionLocal() as db:
        count = await generate_nightly_insights(db, user_id=user_id)
    logger.info(f"Insight job: generated {count} new insights")


async def run_habit_cycle():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_habit_cycle_summary
    async with AsyncSessionLocal() as db:
        await send_habit_cycle_summary(db)


async def run_rank_change():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_rank_change_notifications
    async with AsyncSessionLocal() as db:
        await send_rank_change_notifications(db)


async def run_monthly():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import create_next_monthly_challenge_and_enroll_users
    async with AsyncSessionLocal() as db:
        await create_next_monthly_challenge_and_enroll_users(db)


async def run_test_push():
    from app.db.session import AsyncSessionLocal
    from app.services.reminder_service import send_test_notification_to_user
    async with AsyncSessionLocal() as db:
        await send_test_notification_to_user(db)


# ─────────────────────────────────────────────────────────────────────────────
# Job registry
# ─────────────────────────────────────────────────────────────────────────────

JOBS: dict[str, tuple[str, object]] = {
    "ranks":         ("Rank snapshot (previous_rank update)",        run_ranks),
    "streak":        ("Streak-at-risk push alerts",                  run_streak),
    "reminders":     ("Evening step-count push reminders",           run_reminders),
    "nudge":         ("Challenge step nudges",                       run_nudge),
    "habit_morning": ("Habit morning reminder push",                 run_habit_morning),
    "habit_evening": ("Habit evening nudge push",                    run_habit_evening),
    "weekly":        ("Weekly habit/step summary push",              run_weekly),
    "insight":       ("Nightly AI insight generation (all users)",   run_insight),
    "habit_cycle":   ("Habit cycle completion summary push",         run_habit_cycle),
    "rank_change":   ("Rank-change push notifications",              run_rank_change),
    "monthly":       ("Monthly challenge auto-creation",             run_monthly),
    "test_push":     ("Sample test push to test user",               run_test_push),
}

ALL_JOBS = list(JOBS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(job_names: list[str], sql_debug: bool):
    if sql_debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    # Expand "all"
    to_run = ALL_JOBS if "all" in job_names else job_names

    results: list[dict] = []

    for name in to_run:
        label, fn = JOBS[name]
        _hdr(f"{name}  —  {label}")
        t0 = time.monotonic()
        try:
            # insight supports optional --user-id
            if name == "insight" and args.user_id:
                await run_insight(user_id=args.user_id)
            else:
                await fn()
            elapsed = time.monotonic() - t0
            _ok(name, elapsed)
            results.append({"name": name, "ok": True, "elapsed": elapsed})
        except Exception as exc:
            elapsed = time.monotonic() - t0
            _fail(name, str(exc), elapsed)
            results.append({"name": name, "ok": False, "elapsed": elapsed, "error": str(exc)})

    # ── summary ───────────────────────────────────────────────────────────────
    if len(results) > 1:
        print(f"\n{_BOLD}{'─'*60}{_RST}")
        print(f"  {_BOLD}SUMMARY{_RST}")
        print(f"{_BOLD}{'─'*60}{_RST}")
        for r in results:
            status = f"{_GRN}OK  {_RST}" if r["ok"] else f"{_RED}FAIL{_RST}"
            print(f"  {status}  {r['elapsed']:5.2f}s  {r['name']}")
            if not r["ok"]:
                print(f"         {_DIM}{r.get('error','')[:80]}{_RST}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manually trigger scheduler jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "jobs",
        nargs="+",
        choices=ALL_JOBS + ["all"],
        metavar="JOB",
        help=f"Job(s) to run. Choices: {', '.join(ALL_JOBS + ['all'])}",
    )
    parser.add_argument(
        "--user-id",
        metavar="UUID",
        default=None,
        help="For the 'insight' job: generate only for this user UUID instead of all users",
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="Show raw SQL queries (SQLAlchemy echo)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.jobs, args.sql))
