"""
Seed sample notifications into notification_inbox for a user.

Usage (from project root):
    python scripts/seed_notifications.py
    python scripts/seed_notifications.py --user-id <uuid>
    python scripts/seed_notifications.py --user-id <uuid> --clear
    python scripts/seed_notifications.py --user-id <uuid> --view

Options:
    --user-id   Target user UUID (defaults to first user in DB)
    --clear     Delete existing inbox rows for that user before seeding
    --view      Only view existing notifications, do not seed
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

_IST = ZoneInfo("Asia/Kolkata")

# ── Sample notification definitions ──────────────────────────────────────────
# Each entry maps to one row in notification_inbox.
# expires_days=None means the row never expires.
SAMPLES = [
    # ── Achievement / System ─────────────────────────────────────────────────
    {
        "type":         "perfect_day",
        "template_key": "perfect_day_v1",
        "payload":      {"name": "Honey"},
        "action_url":   "/socialapp/habits",
        "push_title":   "Perfect day, Honey! 🔥",
        "push_body":    "Every habit done. You're on fire today!",
        "is_read":      False,
        "expires_days": None,
        "ago_hours":    1,
    },
    {
        "type":         "habit_milestone",
        "template_key": "habit_milestone_v1",
        "payload":      {"name": "Honey", "streak": 7},
        "action_url":   "/socialapp/habits",
        "push_title":   "7-day streak! 🏆",
        "push_body":    "One full week of habits. Keep it going!",
        "is_read":      False,
        "expires_days": None,
        "ago_hours":    5,
    },
    {
        "type":         "habit_milestone",
        "template_key": "habit_milestone_v1",
        "payload":      {"name": "Honey", "streak": 3},
        "action_url":   "/socialapp/habits",
        "push_title":   "3-day streak! 🌟",
        "push_body":    "You've built a 3-day habit streak!",
        "is_read":      True,
        "expires_days": None,
        "ago_hours":    72,
    },
    {
        "type":         "rank_up",
        "template_key": "rank_up_v1",
        "payload":      {"name": "Honey", "rank": 2, "moved": 3},
        "action_url":   "/socialapp/challanges/1/steps",
        "push_title":   "You climbed to rank #2! 🚀",
        "push_body":    "You moved up 3 spots on the leaderboard.",
        "is_read":      False,
        "expires_days": 30,
        "ago_hours":    10,
    },
    {
        "type":         "weekly_summary",
        "template_key": "weekly_summary_v1",
        "payload":      {"name": "Honey", "steps": 52340, "habit_pct": 85},
        "action_url":   "/socialapp",
        "push_title":   "Your weekly summary is ready 📊",
        "push_body":    "52,340 steps · 85% habits this week. Nice work!",
        "is_read":      True,
        "expires_days": 90,
        "ago_hours":    48,
    },
    {
        "type":         "habit_cycle",
        "template_key": "habit_cycle_v1",
        "payload":      {"name": "Honey", "habit_pct": 78, "perfect_days": 4, "done_days": 22, "possible_days": 28},
        "action_url":   "/socialapp/habits",
        "push_title":   "7-day habit challenge complete! 🎯",
        "push_body":    "78% completion · 4 perfect days. Great effort!",
        "is_read":      True,
        "expires_days": 90,
        "ago_hours":    96,
    },
    # ── Social ───────────────────────────────────────────────────────────────
    {
        "type":         "partner_request",
        "template_key": "partner_request_v1",
        "payload":      {"requester_name": "Arjun", "requester_id": "00000000-0000-0000-0000-000000000001"},
        "action_url":   "/socialapp/partners",
        "push_title":   None,
        "push_body":    None,
        "actor_name":   "Arjun",
        "is_read":      False,
        "expires_days": None,
        "ago_hours":    2,
    },
    {
        "type":         "partner_accepted",
        "template_key": "partner_accepted_v1",
        "payload":      {"partner_name": "Priya", "partner_id": "00000000-0000-0000-0000-000000000002"},
        "action_url":   "/socialapp/partners",
        "push_title":   "Priya accepted your partner request 🤝",
        "push_body":    "You're now accountability partners!",
        "actor_name":   "Priya",
        "is_read":      False,
        "expires_days": 30,
        "ago_hours":    3,
    },
    {
        "type":         "partner_nudge",
        "template_key": "partner_nudge_v1",
        "payload":      {"sender_name": "Priya", "sender_id": "00000000-0000-0000-0000-000000000002"},
        "action_url":   "/socialapp/habits",
        "push_title":   "Priya is cheering you on 🤝",
        "push_body":    "You have habits to complete today. Finish them strong!",
        "actor_name":   "Priya",
        "is_read":      True,
        "expires_days": 30,
        "ago_hours":    8,
    },
]


async def seed(user_id: str, clear: bool):
    async with AsyncSessionLocal() as db:
        # Resolve user
        row = await db.execute(
            text("SELECT id, name FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user = row.mappings().first()
        if not user:
            print(f"{YELLOW}User {user_id} not found.{RESET}")
            return

        name = (user["name"] or "there").split()[0]
        print(f"\n{BOLD}Seeding notifications for:{RESET} {name} ({user_id})")

        if clear:
            result = await db.execute(
                text("DELETE FROM notification_inbox WHERE user_id = :uid"),
                {"uid": user_id},
            )
            print(f"{DIM}  Cleared {result.rowcount} existing rows{RESET}")

        now = datetime.now(tz=timezone.utc)
        inserted = 0

        for s in SAMPLES:
            created_at = now - timedelta(hours=s["ago_hours"])
            expires_at = (
                created_at + timedelta(days=s["expires_days"])
                if s.get("expires_days") else None
            )
            import json
            await db.execute(text("""
                INSERT INTO notification_inbox
                    (user_id, type, actor_name, template_key, payload,
                     action_url, push_title, push_body, is_read, created_at, expires_at)
                VALUES
                    (:uid, :type, :actor_name, :template_key, CAST(:payload AS jsonb),
                     :action_url, :push_title, :push_body, :is_read, :created_at, :expires_at)
            """), {
                "uid":          user_id,
                "type":         s["type"],
                "actor_name":   s.get("actor_name"),
                "template_key": s["template_key"],
                "payload":      json.dumps(s["payload"]),
                "action_url":   s.get("action_url"),
                "push_title":   s.get("push_title"),
                "push_body":    s.get("push_body"),
                "is_read":      s["is_read"],
                "created_at":   created_at,
                "expires_at":   expires_at,
            })
            read_label = f"{DIM}read{RESET}" if s["is_read"] else f"{GREEN}unread{RESET}"
            print(f"  {CYAN}{s['type']:<20}{RESET}  {read_label}  {DIM}{s['push_title'] or '(no push)'}{RESET}")
            inserted += 1

        await db.commit()
        print(f"\n{GREEN}{BOLD}Done.{RESET} Inserted {inserted} notifications.\n")

        # Show unread count
        cnt = await db.execute(
            text("SELECT COUNT(*) FROM notification_inbox WHERE user_id = :uid AND is_read = false"),
            {"uid": user_id},
        )
        print(f"Bell badge will show: {BOLD}{cnt.scalar()}{RESET} unread\n")


async def get_first_user() -> str:
    async with AsyncSessionLocal() as db:
        row = await db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1"))
        uid = row.scalar_one_or_none()
        if not uid:
            print("No users found in DB.")
            sys.exit(1)
        return str(uid)


async def view(user_id: str):
    import json as _json
    async with AsyncSessionLocal() as db:
        # Resolve user
        row = await db.execute(
            text("SELECT id, name FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user = row.mappings().first()
        if not user:
            print(f"{YELLOW}User {user_id} not found.{RESET}")
            return

        name = (user["name"] or "there").split()[0]

        # Unread count
        cnt_row = await db.execute(
            text("SELECT COUNT(*) FROM notification_inbox WHERE user_id = :uid AND is_read = false AND (expires_at IS NULL OR expires_at > now())"),
            {"uid": user_id},
        )
        unread = cnt_row.scalar() or 0

        print(f"\n{BOLD}Notifications for:{RESET} {name} ({user_id})")
        print(f"Bell badge: {GREEN}{BOLD}{unread} unread{RESET}\n")

        rows = await db.execute(text("""
            SELECT id, type, template_key, actor_name, payload,
                   push_title, push_body, action_url, is_read, created_at, expires_at
            FROM   notification_inbox
            WHERE  user_id = :uid
            ORDER  BY created_at DESC
        """), {"uid": user_id})

        items = list(rows.mappings())
        if not items:
            print(f"{DIM}  No notifications found.{RESET}\n")
            return

        print(f"{'#':<4} {'TYPE':<22} {'STATUS':<8} {'TITLE':<45} {'CREATED'}")
        print("─" * 110)
        for i, r in enumerate(items, 1):
            read_label = f"{DIM}read  {RESET}" if r["is_read"] else f"{GREEN}unread{RESET}"
            title      = (r["push_title"] or "(no push)")[:43]
            created    = str(r["created_at"])[:16]
            expired    = f" {YELLOW}[expired]{RESET}" if r["expires_at"] and r["expires_at"] < datetime.now(tz=timezone.utc) else ""
            print(f"{i:<4} {CYAN}{r['type']:<22}{RESET} {read_label}  {title:<45} {DIM}{created}{RESET}{expired}")
            # Show payload inline
            try:
                p = _json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                print(f"     {DIM}payload: {p}  →  {r['action_url']}{RESET}")
            except Exception:
                pass
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", help="Target user UUID")
    parser.add_argument("--clear", action="store_true", help="Clear existing inbox before seeding")
    parser.add_argument("--view",  action="store_true", help="View existing notifications only, skip seeding")
    args = parser.parse_args()

    user_id = args.user_id or asyncio.run(get_first_user())

    if args.view:
        asyncio.run(view(user_id))
    else:
        asyncio.run(seed(user_id, args.clear))
