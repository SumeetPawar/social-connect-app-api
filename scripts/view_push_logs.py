"""
Push Notification Delivery Viewer
Usage:
    python scripts/view_push_logs.py           # last 7 days
    python scripts/view_push_logs.py 30        # last 30 days
    python scripts/view_push_logs.py 1         # today only
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

# -- terminal colours ----------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# -- job metadata: label + type ------------------------------------------------
JOB_META = {
    # Scheduled (fire for ALL qualifying users via cron)
    "step_reminder":          ("Evening Step Reminder",       "scheduled",   "steps"),
    "streak_at_risk":         ("Streak At Risk Alert",        "scheduled",   "steps"),
    "challenge_nudge":        ("Challenge Step Nudge",        "scheduled",   "steps"),
    "habit_morning_reminder": ("Habit Morning Reminder",      "scheduled",   "habits"),
    "habit_evening_nudge":    ("Habit Evening Nudge",         "scheduled",   "habits"),
    "weekly_summary":         ("Weekly Summary",              "scheduled",   "both"),
    "rank_change":            ("Rank Change Alert",           "scheduled",   "steps"),
    "habit_cycle_summary":    ("Habit Cycle Completion",      "scheduled",   "habits"),
    "body_scan_reminder":     ("Body Scan Reminder",          "scheduled",   "body"),
    # Real-time (fire immediately on user action)
    "perfect_day":            ("Perfect Day Celebration",     "realtime",    "habits"),
    "streak_milestone":       ("Streak Milestone",            "realtime",    "habits"),
    # System
    "service_startup":        ("Service Startup",             "system",      "system"),
    "test_notification":      ("Test Notification",           "system",      "system"),
}

def _bar(pct: int, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    color  = GREEN if pct >= 90 else (YELLOW if pct >= 70 else RED)
    return color + "#" * filled + DIM + "." * (width - filled) + RESET

def _result_icon(result: str) -> str:
    return {"ok": f"{GREEN}OK {RESET}", "expired": f"{YELLOW}EXP{RESET}", "error": f"{RED}ERR{RESET}"}.get(result, "?")

async def main(days: int):
    since = date.today() - timedelta(days=days)

    async with AsyncSessionLocal() as db:

        # -- 1. overall summary -----------------------------------------------
        ov = (await db.execute(text("""
            SELECT
                COUNT(*)                                   AS total,
                COUNT(*) FILTER (WHERE result='ok')        AS delivered,
                COUNT(*) FILTER (WHERE result='expired')   AS expired,
                COUNT(*) FILTER (WHERE result='error')     AS errors,
                MIN(sent_at)                               AS first,
                MAX(sent_at)                               AS last
            FROM push_logs WHERE sent_at >= :since
        """), {"since": since})).mappings().first()

        total     = ov["total"] or 0
        delivered = ov["delivered"] or 0
        expired   = ov["expired"] or 0
        errors    = ov["errors"] or 0
        pct       = round(delivered / total * 100) if total else 0

        print(f"\n{BOLD}{'='*62}{RESET}")
        print(f"{BOLD}  PUSH NOTIFICATION DASHBOARD  --  last {days} day(s){RESET}")
        print(f"{BOLD}{'='*62}{RESET}")
        print(f"  Period : {since}  -->  {date.today()}")
        if ov["first"]:
            print(f"  Range  : {str(ov['first'])[:19]}  -->  {str(ov['last'])[:19]}")
        print(f"\n  {BOLD}Overall delivery rate:{RESET}")
        print(f"  {_bar(pct)}  {BOLD}{pct}%{RESET}  ({delivered}/{total} sent)")
        if expired: print(f"  {YELLOW}[!] {expired} expired subscriptions auto-removed{RESET}")
        if errors:  print(f"  {RED}[x] {errors} send errors{RESET}")

        # -- 2. by job --------------------------------------------------------
        job_rows = (await db.execute(text("""
            SELECT
                job,
                COUNT(*)                                   AS total,
                COUNT(*) FILTER (WHERE result='ok')        AS delivered,
                COUNT(*) FILTER (WHERE result='expired')   AS expired,
                COUNT(*) FILTER (WHERE result='error')     AS errors,
                MAX(sent_at)                               AS last_fired,
                COUNT(DISTINCT user_id)                    AS unique_users
            FROM push_logs
            WHERE sent_at >= :since
            GROUP BY job
            ORDER BY last_fired DESC
        """), {"since": since})).mappings().all()

        print(f"\n{BOLD}  BY JOB{RESET}  {'-'*54}")

        # Group by type
        groups = {"scheduled": [], "realtime": [], "system": []}
        for r in job_rows:
            meta  = JOB_META.get(r["job"], (r["job"], "scheduled", "?"))
            gtype = meta[1]
            groups.setdefault(gtype, []).append((r, meta))

        group_labels = {"scheduled": "[CRON] SCHEDULED JOBS", "realtime": "[NOW]  REAL-TIME (on action)", "system": "[SYS]  SYSTEM"}
        for gkey, glabel in group_labels.items():
            items = groups.get(gkey, [])
            if not items:
                continue
            print(f"\n  {CYAN}{glabel}{RESET}")
            for r, meta in items:
                label, _, channel = meta
                t   = r["total"]
                d   = r["delivered"]
                p   = round(d / t * 100) if t else 0
                col = GREEN if p >= 90 else (YELLOW if p >= 70 else RED)
                last = str(r["last_fired"])[:16] if r["last_fired"] else "never"
                exp  = f" {YELLOW}+{r['expired']}exp{RESET}" if r["expired"] else ""
                err  = f" {RED}+{r['errors']}err{RESET}"     if r["errors"]  else ""
                print(f"  {col}{p:3d}%{RESET} {_bar(p, 12)}  {label:<28} {d:>3}/{t:<3}  {DIM}{last}{RESET}{exp}{err}")
                print(f"       {DIM}channel={channel}  users={r['unique_users']}{RESET}")

        # -- 3. by day --------------------------------------------------------
        day_rows = (await db.execute(text("""
            SELECT
                sent_at::date                              AS day,
                COUNT(*)                                   AS total,
                COUNT(*) FILTER (WHERE result='ok')        AS delivered,
                COUNT(*) FILTER (WHERE result='error')     AS errors,
                array_agg(DISTINCT job ORDER BY job)       AS jobs_fired
            FROM push_logs
            WHERE sent_at >= :since
            GROUP BY sent_at::date
            ORDER BY day DESC
        """), {"since": since})).mappings().all()

        print(f"\n{BOLD}  BY DAY{RESET}  {'-'*54}")
        if not day_rows:
            print(f"  {DIM}No notifications sent in this period.{RESET}")
        for r in day_rows:
            t   = r["total"]
            d   = r["delivered"]
            p   = round(d / t * 100) if t else 0
            col = GREEN if p >= 90 else (YELLOW if p >= 70 else RED)
            jobs_str = ", ".join(r["jobs_fired"] or [])
            is_today = str(r["day"]) == str(date.today())
            tag = f" {BOLD}<-- today{RESET}" if is_today else ""
            print(f"  {col}{str(r['day'])}{RESET}  {d:>3}/{t} ok  {DIM}{jobs_str}{RESET}{tag}")

        # -- 4. jobs that NEVER fired in this period --------------------------
        fired_jobs   = {r["job"] for r in job_rows}
        scheduled_jobs = {j for j, (_, t, _) in JOB_META.items() if t == "scheduled"}
        silent_jobs    = scheduled_jobs - fired_jobs

        if silent_jobs:
            print(f"\n{BOLD}  JOBS THAT NEVER FIRED  {RESET}{'-'*38}")
            for j in sorted(silent_jobs):
                label = JOB_META[j][0]
                print(f"  {RED}[x] {label}{RESET}  {DIM}({j}){RESET}")

        # -- 5. recent 20 entries ---------------------------------------------
        recent_rows = (await db.execute(text("""
            SELECT pl.job, pl.result, pl.title, pl.sent_at, u.name AS uname,
                   pl.error_detail
            FROM push_logs pl
            JOIN users u ON u.id = pl.user_id
            WHERE pl.sent_at >= :since
            ORDER BY pl.sent_at DESC
            LIMIT 20
        """), {"since": since})).mappings().all()

        print(f"\n{BOLD}  RECENT SENDS (last 20){RESET}  {'-'*38}")
        if not recent_rows:
            print(f"  {DIM}No records.{RESET}")
        for r in recent_rows:
            icon  = _result_icon(r["result"])
            ts    = str(r["sent_at"])[:16]
            title = (r["title"] or "")[:40]
            print(f"  {icon} {DIM}{ts}{RESET}  {r['job']:<26}  {DIM}{r['uname'][:12]:<12}{RESET}  {title}")
            if r["result"] == "error" and r["error_detail"]:
                print(f"       {RED}└─ {r['error_detail']}{RESET}")

        print(f"\n{'='*62}\n")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    asyncio.run(main(days))
