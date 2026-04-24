"""
Show step-challenge leaderboard rankings from the database.

Usage:
  python scripts/challenge_rankings.py                         # list active challenges
  python scripts/challenge_rankings.py --list                  # list all challenges
  python scripts/challenge_rankings.py --challenge-id <uuid>   # show rankings for one challenge
  python scripts/challenge_rankings.py --all                   # show rankings for all active challenges

Examples:
  python scripts/challenge_rankings.py
  python scripts/challenge_rankings.py --challenge-id 8e3f1a2b-...
  python scripts/challenge_rankings.py --all
"""

import asyncio
import asyncpg
import argparse
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

DB_URL = os.environ.get('DATABASE_URL')
if not DB_URL:
    sys.exit("❌  DATABASE_URL environment variable is not set.")
# asyncpg needs postgresql://, not postgresql+asyncpg://
DB_URL = DB_URL.replace('postgresql+asyncpg://', 'postgresql://')


# ── helpers ───────────────────────────────────────────────────────────────────

async def fetch_challenges(conn, status_filter=None):
    where = "WHERE c.status = $1" if status_filter else ""
    args = [status_filter] if status_filter else []
    rows = await conn.fetch(f"""
        SELECT c.id, c.title, c.status, c.start_date, c.end_date,
               COUNT(cp.id) AS participants
        FROM challenges c
        LEFT JOIN challenge_participants cp
               ON cp.challenge_id = c.id AND cp.left_at IS NULL
        {where}
        GROUP BY c.id, c.title, c.status, c.start_date, c.end_date
        ORDER BY c.start_date DESC
    """, *args)
    return rows


async def fetch_leaderboard(conn, challenge_id: str):
    today = date.today()

    challenge = await conn.fetchrow(
        "SELECT id, title, start_date, end_date, status FROM challenges WHERE id = $1",
        challenge_id
    )
    if not challenge:
        print(f"❌  Challenge {challenge_id} not found.")
        return

    start_date = challenge['start_date']
    end_date = challenge['end_date']
    end_or_today = min(end_date, today)
    total_days = max((end_or_today - start_date).days + 1, 1)
    days_left = max((end_date - today).days, 0)

    rows = await conn.fetch("""
        WITH user_totals AS (
            SELECT
                cp.user_id,
                u.name,
                u.email,
                cp.selected_daily_target,
                cp.challenge_current_streak,
                cp.challenge_longest_streak,
                cp.previous_rank,
                COALESCE(SUM(ds.steps), 0)                                        AS total_steps,
                COALESCE(AVG(ds.steps), 0)                                        AS avg_steps,
                COUNT(DISTINCT ds.day) FILTER (
                    WHERE ds.steps >= cp.selected_daily_target
                )                                                                  AS days_met_goal,
                COUNT(DISTINCT ds.day)                                             AS days_logged
            FROM challenge_participants cp
            JOIN users u ON u.id = cp.user_id
            LEFT JOIN daily_steps ds
                   ON ds.user_id = cp.user_id
                  AND ds.day >= $2
                  AND ds.day <= $3
            WHERE cp.challenge_id = $1
              AND cp.left_at IS NULL
            GROUP BY cp.user_id, u.name, u.email, cp.selected_daily_target,
                     cp.challenge_current_streak, cp.challenge_longest_streak,
                     cp.previous_rank
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY total_steps DESC, user_id ASC) AS rank,
            user_id, name, email,
            selected_daily_target AS goal,
            challenge_current_streak AS streak,
            challenge_longest_streak AS longest_streak,
            previous_rank,
            total_steps,
            ROUND(avg_steps)                                           AS avg_steps,
            days_met_goal,
            days_logged,
            CASE WHEN $4 > 0
                 THEN ROUND((days_met_goal::numeric / $4) * 100, 1)
                 ELSE 0
            END                                                        AS completion_pct
        FROM user_totals
        ORDER BY rank
    """, challenge_id, start_date, end_or_today, total_days)

    # ── print header ──────────────────────────────────────────────────────────
    print()
    print("=" * 110)
    print(f"  {challenge['title']}")
    print(f"  {start_date}  →  {end_date}    status={challenge['status']}    days left={days_left}    participants={len(rows)}")
    print("=" * 110)
    print(
        f"{'Rank':<5} {'Δ':<5} {'Name':<28} {'Email':<32} "
        f"{'Steps':>9} {'Avg/day':>8} {'Goal':>6} "
        f"{'Streak':>7} {'Best':>5} {'Met':>4} {'Consist%':>9}"
    )
    print("-" * 110)

    for r in rows:
        prev = r['previous_rank']
        if prev:
            delta = prev - r['rank']
            delta_str = (f"+{delta}" if delta > 0 else str(delta)) if delta != 0 else "="
        else:
            delta_str = "new"

        name  = (r['name'] or '—')[:27]
        email = (r['email'] or '—')[:31]
        longest = max(r['longest_streak'] or 0, r['streak'] or 0)

        print(
            f"{r['rank']:<5} {delta_str:<5} {name:<28} {email:<32} "
            f"{r['total_steps']:>9,} {int(r['avg_steps']):>8,} {r['goal'] or '-':>6} "
            f"{r['streak'] or 0:>7} {longest:>5} {r['days_met_goal']:>4} "
            f"{float(r['completion_pct']):>8.1f}%"
        )

    print()


# ── commands ──────────────────────────────────────────────────────────────────

async def cmd_list(all_challenges: bool):
    conn = await asyncpg.connect(DB_URL)
    status = None if all_challenges else 'active'
    rows = await fetch_challenges(conn, status_filter=status)
    await conn.close()

    label = "All" if all_challenges else "Active"
    print(f"\n{label} challenges ({len(rows)}):\n")
    print(f"{'ID':<38} {'Title':<28} {'Status':<10} {'Start':<12} {'End':<12} {'Participants':>12}")
    print("-" * 118)
    for r in rows:
        print(
            f"{str(r['id']):<38} {str(r['title']):<28} {r['status']:<10} "
            f"{str(r['start_date']):<12} {str(r['end_date']):<12} {r['participants']:>12}"
        )
    print()


async def cmd_rankings(challenge_id: str):
    conn = await asyncpg.connect(DB_URL)
    await fetch_leaderboard(conn, challenge_id)
    await conn.close()


async def cmd_all_active():
    conn = await asyncpg.connect(DB_URL)
    rows = await fetch_challenges(conn, status_filter='active')
    if not rows:
        print("No active challenges found.")
        await conn.close()
        return
    for r in rows:
        await fetch_leaderboard(conn, str(r['id']))
    await conn.close()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="View step challenge leaderboard rankings"
    )
    parser.add_argument("--challenge-id", metavar="UUID",
                        help="Show rankings for a specific challenge")
    parser.add_argument("--all", action="store_true",
                        help="Show rankings for all active challenges")
    parser.add_argument("--list", action="store_true",
                        help="List all challenges (not just active)")
    args = parser.parse_args()

    if args.challenge_id:
        asyncio.run(cmd_rankings(args.challenge_id))
    elif args.all:
        asyncio.run(cmd_all_active())
    elif args.list:
        asyncio.run(cmd_list(all_challenges=True))
    else:
        # Default: list active challenges
        asyncio.run(cmd_list(all_challenges=False))
