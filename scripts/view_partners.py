"""
Partner Admin Tool
==================
Usage:
    python scripts/view_partners.py list                        # active/pending pairs (all depts)
    python scripts/view_partners.py delete_pair <pair_id>       # hard delete pair (test data cleanup)
    python scripts/view_partners.py stats                       # dept-level summary
    python scripts/view_partners.py list --all                  # all pairs incl. reshuffled/completed
    python scripts/view_partners.py list <dept_id>              # pairs for one dept
    python scripts/view_partners.py list <dept_id> --all        # all pairs for one dept
    python scripts/view_partners.py unmatched                   # users with no partner
    python scripts/view_partners.py user <user_id>              # all pairs for a user
    python scripts/view_partners.py messages <pair_id>          # last 20 chat messages
    python scripts/view_partners.py clear_pair <pair_id>        # mark pair as reshuffled (soft close)
    python scripts/view_partners.py clear_user <user_id>        # close all pairs for user
   

Note: pair_id and user_id must be integers/UUIDs as shown in list output.
      asyncpg requires exact types — always pass numeric pair_id as a number, not quoted.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.db.session import AsyncSessionLocal

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

STATUS_COLOR = {
    "approved":   GREEN,
    "pending":    YELLOW,
    "rejected":   DIM,
    "blocked":    RED,
    "completed":  DIM,
    "reshuffled": DIM,
}

TYPE_TAG = {
    "manual": "[manual]",
    "admin":  "[admin] ",
    "auto":   "[auto]  ",
}


def _sc(status):
    return STATUS_COLOR.get(status, "") + status + RESET


def _tt(atype):
    return CYAN + TYPE_TAG.get(atype, atype) + RESET


async def list_pairs(dept_id=None, show_all=False):
    async with AsyncSessionLocal() as db:
        params = {}
        dept_clause = ""
        status_clause = "" if show_all else "AND ap.status IN ('approved', 'pending')"
        if dept_id:
            dept_clause = "AND u1.department_id = :dept AND u2.department_id = :dept"
            params["dept"] = dept_id

        rows = (await db.execute(text(f"""
            SELECT
                ap.id,
                ap.status,
                ap.assignment_type,
                ap.week_start,
                ap.approved_at,
                ap.requester_keep,
                ap.partner_keep,
                ap.keep_deadline,
                u1.name  AS user_a,
                u1.id    AS uid_a,
                u2.name  AS user_b,
                u2.id    AS uid_b,
                d.name   AS dept_name,
                (SELECT COUNT(*) FROM partner_messages pm WHERE pm.pair_id = ap.id) AS msg_count
            FROM accountability_partners ap
            JOIN users u1 ON u1.id = ap.requester_id
            JOIN users u2 ON u2.id = ap.partner_id
            JOIN departments d ON d.id = u1.department_id
            WHERE 1=1 {status_clause} {dept_clause}
            ORDER BY ap.status, ap.approved_at DESC NULLS LAST
        """), params)).mappings().all()

        if not rows:
            print(f"\n  {DIM}No partner pairs found.{RESET}\n")
            return

        print(f"\n{BOLD}{'='*80}{RESET}")
        print(f"{BOLD}  PARTNER PAIRS  ({len(rows)} total){RESET}")
        print(f"{BOLD}{'='*80}{RESET}\n")

        cur_status = None
        for r in rows:
            if r["status"] != cur_status:
                cur_status = r["status"]
                print(f"  {BOLD}{_sc(cur_status).upper()}{RESET}  {'-'*60}")

            keep = ""
            if r["requester_keep"] is not None or r["partner_keep"] is not None:
                rv = "Y" if r["requester_keep"] else ("N" if r["requester_keep"] is False else "?")
                pv = "Y" if r["partner_keep"]   else ("N" if r["partner_keep"]   is False else "?")
                keep = f"  {YELLOW}vote={rv}/{pv}{RESET}"

            week = f"  week={r['week_start']}" if r["week_start"] else ""
            msgs = f"  {DIM}{r['msg_count']} msgs{RESET}" if r["msg_count"] else ""

            print(f"  id={BOLD}{r['id']:<4}{RESET}  {_tt(r['assignment_type'])}  "
                  f"{r['user_a'] or 'N/A':<18} <-> {r['user_b'] or 'N/A':<18}  "
                  f"{DIM}{r['dept_name']:<20}{RESET}{week}{keep}{msgs}")

        print()


async def list_unmatched(dept_id=None):
    async with AsyncSessionLocal() as db:
        params = {}
        dept_clause = "AND u.department_id = :dept" if dept_id else ""
        if dept_id:
            params["dept"] = dept_id

        rows = (await db.execute(text(f"""
            SELECT u.id, u.name, u.created_at, d.name AS dept_name
            FROM users u
            JOIN departments d ON d.id = u.department_id
            WHERE 1=1 {dept_clause}
              AND u.id NOT IN (
                  SELECT requester_id FROM accountability_partners
                  WHERE status IN ('approved','pending')
                  UNION
                  SELECT partner_id FROM accountability_partners
                  WHERE status IN ('approved','pending')
              )
            ORDER BY d.name, u.name
        """), params)).mappings().all()

        print(f"\n{BOLD}{'='*70}{RESET}")
        print(f"{BOLD}  UNMATCHED USERS  ({len(rows)} total){RESET}")
        print(f"{BOLD}{'='*70}{RESET}\n")

        if not rows:
            print(f"  {GREEN}All users are paired.{RESET}\n")
            return

        cur_dept = None
        for r in rows:
            if r["dept_name"] != cur_dept:
                cur_dept = r["dept_name"]
                print(f"  {CYAN}{cur_dept}{RESET}")
            print(f"    {str(r['id']):<38}  {r['name'] or 'N/A':<20}  {DIM}joined {str(r['created_at'])[:10]}{RESET}")
        print()


async def user_pairs(user_id):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("""
            SELECT
                ap.id, ap.status, ap.assignment_type, ap.week_start, ap.approved_at,
                CASE WHEN ap.requester_id = :uid THEN u2.name ELSE u1.name END AS partner_name,
                CASE WHEN ap.requester_id = :uid THEN u2.id   ELSE u1.id   END AS partner_id,
                (SELECT COUNT(*) FROM partner_messages pm WHERE pm.pair_id = ap.id) AS msg_count
            FROM accountability_partners ap
            JOIN users u1 ON u1.id = ap.requester_id
            JOIN users u2 ON u2.id = ap.partner_id
            WHERE ap.requester_id = :uid OR ap.partner_id = :uid
            ORDER BY ap.approved_at DESC NULLS LAST
        """), {"uid": user_id})).mappings().all()

        me = (await db.execute(text("SELECT name FROM users WHERE id = :uid"), {"uid": user_id})).scalar()

        print(f"\n{BOLD}  Pairs for {me or user_id}{RESET}\n")
        if not rows:
            print(f"  {DIM}No pairs found.{RESET}\n")
            return

        for r in rows:
            week = f"  week={r['week_start']}" if r["week_start"] else ""
            msgs = f"  {r['msg_count']} msgs" if r["msg_count"] else ""
            print(f"  id={r['id']:<4}  {_sc(r['status']):<12}  {_tt(r['assignment_type'])}  "
                  f"partner: {r['partner_name'] or 'N/A':<20}{week}{msgs}")
        print()


async def show_messages(pair_id):
    async with AsyncSessionLocal() as db:
        pair = (await db.execute(text("""
            SELECT ap.id, u1.name AS user_a, u2.name AS user_b
            FROM accountability_partners ap
            JOIN users u1 ON u1.id = ap.requester_id
            JOIN users u2 ON u2.id = ap.partner_id
            WHERE ap.id = :pid
        """), {"pid": int(pair_id)})).mappings().first()

        if not pair:
            print(f"  {RED}Pair {pair_id} not found.{RESET}\n")
            return

        rows = (await db.execute(text("""
            SELECT pm.id, pm.body, pm.sent_at, pm.read_at, pm.expires_at,
                   u.name AS sender_name
            FROM partner_messages pm
            JOIN users u ON u.id = pm.sender_id
            WHERE pm.pair_id = :pid
            ORDER BY pm.sent_at DESC
            LIMIT 20
        """), {"pid": int(pair_id)})).mappings().all()

        print(f"\n{BOLD}  Messages — pair {pair_id}  ({pair['user_a']} <-> {pair['user_b']}){RESET}\n")
        if not rows:
            print(f"  {DIM}No messages.{RESET}\n")
            return

        for r in reversed(rows):
            ts      = str(r["sent_at"])[:16]
            read    = f"  {DIM}read{RESET}" if r["read_at"] else ""
            expires = f"  {YELLOW}expires {str(r['expires_at'])[:10]}{RESET}" if r["expires_at"] else ""
            print(f"  {DIM}{ts}{RESET}  {BOLD}{r['sender_name']:<16}{RESET}  {r['body'][:60]}{read}{expires}")
        print()


async def clear_pair(pair_id):
    async with AsyncSessionLocal() as db:
        result = (await db.execute(text("""
            UPDATE accountability_partners
            SET status = 'reshuffled'
            WHERE id = :pid AND status IN ('approved','pending')
            RETURNING id
        """), {"pid": int(pair_id)})).scalar()

        if result:
            await db.execute(text("""
                UPDATE partner_messages
                SET expires_at = now() + INTERVAL '30 days'
                WHERE pair_id = :pid AND expires_at IS NULL
            """), {"pid": int(pair_id)})
            await db.commit()
            print(f"\n  {GREEN}Pair {pair_id} marked as reshuffled. Messages expire in 30 days.{RESET}\n")
        else:
            print(f"\n  {YELLOW}Pair {pair_id} not found or already closed.{RESET}\n")


async def clear_user(user_id):
    async with AsyncSessionLocal() as db:
        result = (await db.execute(text("""
            UPDATE accountability_partners
            SET status = 'reshuffled'
            WHERE status IN ('approved','pending')
              AND (requester_id = :uid OR partner_id = :uid)
            RETURNING id
        """), {"uid": user_id})).fetchall()

        pair_ids = [r[0] for r in result]
        for pid in pair_ids:
            await db.execute(text("""
                UPDATE partner_messages
                SET expires_at = now() + INTERVAL '30 days'
                WHERE pair_id = :pid AND expires_at IS NULL
            """), {"pid": pid})

        await db.commit()
        print(f"\n  {GREEN}Closed {len(pair_ids)} pair(s) for user {user_id}.{RESET}\n")


async def delete_pair(pair_id):
    async with AsyncSessionLocal() as db:
        result = (await db.execute(text("""
            DELETE FROM accountability_partners WHERE id = :pid RETURNING id
        """), {"pid": int(pair_id)})).scalar()
        if result:
            await db.commit()
            print(f"\n  {GREEN}Pair {pair_id} permanently deleted.{RESET}\n")
        else:
            print(f"\n  {YELLOW}Pair {pair_id} not found.{RESET}\n")


async def stats():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("""
            SELECT
                d.name                                              AS dept,
                COUNT(*)       FILTER (WHERE ap.status = 'approved')  AS active,
                COUNT(*)       FILTER (WHERE ap.status = 'pending')   AS pending,
                COUNT(*)       FILTER (WHERE ap.status = 'completed') AS completed,
                COUNT(DISTINCT CASE WHEN ap.status IN ('approved','pending')
                               THEN ap.requester_id END)            AS paired_users_a,
                COUNT(DISTINCT CASE WHEN ap.status IN ('approved','pending')
                               THEN ap.partner_id END)              AS paired_users_b,
                (SELECT COUNT(*) FROM users u2
                 WHERE u2.department_id = d.id)                     AS total_users
            FROM departments d
            LEFT JOIN accountability_partners ap
                ON (ap.requester_id IN (SELECT id FROM users WHERE department_id = d.id)
                 OR ap.partner_id   IN (SELECT id FROM users WHERE department_id = d.id))
            GROUP BY d.id, d.name
            ORDER BY d.name
        """))).mappings().all()

        msg_count = (await db.execute(text(
            "SELECT COUNT(*) FROM partner_messages WHERE expires_at IS NULL"
        ))).scalar()

        expired_soon = (await db.execute(text(
            "SELECT COUNT(*) FROM partner_messages WHERE expires_at < now() + INTERVAL '3 days'"
        ))).scalar()

        print(f"\n{BOLD}{'='*70}{RESET}")
        print(f"{BOLD}  PARTNER STATS BY DEPARTMENT{RESET}")
        print(f"{BOLD}{'='*70}{RESET}\n")
        print(f"  {'Dept':<25} {'Users':>6}  {'Active':>7}  {'Pending':>8}  {'Paired%':>8}")
        print(f"  {'-'*60}")

        for r in rows:
            paired = (r["paired_users_a"] or 0) + (r["paired_users_b"] or 0)
            total  = r["total_users"] or 1
            pct    = round(paired / total * 100)
            col    = GREEN if pct >= 80 else (YELLOW if pct >= 50 else RED)
            print(f"  {r['dept']:<25} {total:>6}  {r['active']:>7}  {r['pending']:>8}  "
                  f"{col}{pct:>7}%{RESET}")

        print(f"\n  {BOLD}Messages:{RESET} {msg_count} active  |  "
              f"{YELLOW}{expired_soon} expiring within 3 days{RESET}\n")


# ── entrypoint ────────────────────────────────────────────────────────────────

COMMANDS = {
    "list":        (list_pairs,     "[dept_id] [--all]"),
    "unmatched":   (list_unmatched, "[dept_id]"),
    "user":        (user_pairs,     "<user_id>"),
    "messages":    (show_messages,  "<pair_id>"),
    "clear_pair":  (clear_pair,     "<pair_id>"),
    "clear_user":  (clear_user,     "<user_id>"),
    "delete_pair": (delete_pair,    "<pair_id>"),
    "stats":       (stats,          ""),
}


def usage():
    print("\nUsage: python scripts/view_partners.py <command> [args]\n")
    print("Commands:")
    for cmd, (_, args) in COMMANDS.items():
        print(f"  {cmd:<14} {args}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        usage()
        sys.exit(1)

    cmd = sys.argv[1]
    fn, _ = COMMANDS[cmd]
    args = [a for a in sys.argv[2:] if a != "--all"]
    kwargs = {}
    if cmd == "list" and "--all" in sys.argv[2:]:
        kwargs["show_all"] = True

    try:
        asyncio.run(fn(*args, **kwargs))
    except TypeError:
        print(f"\n  {RED}Wrong arguments for '{cmd}'{RESET}")
        usage()
        sys.exit(1)
