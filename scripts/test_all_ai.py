"""
Comprehensive AI call checker.

Tests all five AI features against one user or every user in the database.

AI features
-----------
  insight   — daily home-screen insight     (ai_insight.get_home_insight)
  coach     — 30-day coaching report        (ai_coach.get_coach_report)
  body      — body composition insight      (ai_recommendations.get_body_insight)
  habits    — habit recommendations         (ai_recommendations.get_habit_recommendations)
  goal      — step-goal suggestion          (ai_recommendations.get_step_goal_suggestion)
  all       — run every feature above       (default)

Usage examples (from the project root)
---------------------------------------

  
  .venv311\Scripts\python.exe scripts\test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type insight
  
  


  # single user, all features
  python scripts/test_all_ai.py --user-id <uuid>

  # all users, all features
  python scripts/test_all_ai.py --all-users

  # single feature, specific user, force azure provider
  python scripts/test_all_ai.py --user-id <uuid> --type coach --provider azure

  # just show collected stats for every user, skip AI calls
  python scripts/test_all_ai.py --all-users --type insight --stats-only

  # run all features for all users (useful for batch warm-up)
  python scripts/test_all_ai.py --all-users --type all

  # All 5 AI features for this user
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

# Single feature
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type insight
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type coach
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type body
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type habits
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type goal

# Just show collected stats (no AI call)
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --stats-only

# Generate nightly insight for this user only
python scripts/test_scheduler.py insight --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

# View all AI table data for this user
python scripts/view_ai_data.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

# Delete today's insight so it regenerates fresh
python scripts/view_ai_data.py --delete-insight --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

# Delete cached recommendations to force fresh AI calls
python scripts/view_ai_data.py --delete-rec body   --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad
python scripts/view_ai_data.py --delete-rec habits --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad
python scripts/view_ai_data.py --delete-rec goal   --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

Step 1 — Check if user has any data:
python scripts/test_all_ai.py --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad --type insight --stats-only

Step 2 — Delete today's stored (fallback) insight:
python scripts/view_ai_data.py --delete-insight --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad

Step 3 — Re-generate with fresh AI call:
python scripts/test_scheduler.py insight --user-id 1562c63f-3340-4b41-89f9-caa0e53415ad


"""

import asyncio
import argparse
import json
import os
import sys
import textwrap
import time
from pathlib import Path

# ── make sure project root is importable ─────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── silence SQLAlchemy query logging (echo=True is set in session.py) ─────────
import logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("app.db.session").setLevel(logging.WARNING)
# patch engine.echo AFTER import so echo=True in session.py is overridden
import app.db.session as _db_session
_db_session.engine.echo = False

# ── ANSI helpers ──────────────────────────────────────────────────────────────
_RST  = "\033[0m"
_BOLD = "\033[1m"
_DIM  = "\033[2m"
_GRN  = "\033[32m"
_RED  = "\033[31m"
_YEL  = "\033[33m"
_CYN  = "\033[36m"
_MAG  = "\033[35m"
_BLU  = "\033[34m"

_COLOR_MAP = {
    "purple": _MAG, "green": _GRN, "orange": _YEL,
    "rose": _RED, "teal": _CYN,
}

FEATURE_ORDER = ["insight", "coach", "body", "habits", "goal"]


# ─────────────────────────────────────────────────────────────────────────────
# Pretty-printers
# ─────────────────────────────────────────────────────────────────────────────

def _hdr(text: str, char: str = "─") -> None:
    print(f"\n{_BOLD}{char * 60}{_RST}")
    print(f"  {_BOLD}{text}{_RST}")
    print(f"{_BOLD}{char * 60}{_RST}\n")


def _ok(label: str, msg: str = "") -> None:
    print(f"  {_GRN}✔ {label}{_RST}  {_DIM}{msg}{_RST}")


def _fail(label: str, err: str) -> None:
    print(f"  {_RED}✘ {label}{_RST}  {_DIM}{err}{_RST}")


def _cached_hint(result: dict) -> str:
    return f"{_YEL}[CACHED]{_RST}" if result.get("cached") else f"{_GRN}[FRESH]{_RST}"


def _render_spans(spans: list) -> str:
    out = ""
    for span in (spans or []):
        prefix = _COLOR_MAP.get(span.get("color", ""), "")
        if span.get("style") in ("stat", "milestone"):
            prefix += _BOLD
        out += prefix + span.get("text", "") + (_RST if prefix else "")
    return out


def _print_insight(result: dict) -> None:
    print(f"    Badge   : {_BOLD}[{result.get('badge', '')}]{_RST}  {_cached_hint(result)}")
    print(f"    Headline: {_render_spans(result.get('segments', []))}")
    print(f"    Detail  : {_render_spans(result.get('detail', []))}")
    print(f"    Hook    : {_CYN}{result.get('hook', '')}{_RST}")


def _print_coach(result: dict) -> None:
    print(f"    {_cached_hint(result)}")
    print(f"    Summary : {textwrap.fill(result.get('summary',''), 80, subsequent_indent='              ')}")
    print(f"    Focus   : {_BOLD}{result.get('focus','')}{_RST}")
    ww = result.get("went_well", [])
    im = result.get("improve", [])
    print(f"    Wins ({len(ww)}): " + " | ".join(w.get("title","") for w in ww))
    print(f"    Gaps ({len(im)}): " + " | ".join(i.get("title","") for i in im))


def _print_body(result: dict) -> None:
    if result is None:
        print(f"    {_YEL}[NO DATA] No body-metrics scans found for this user.{_RST}")
        return
    print(f"    {_cached_hint(result)}")
    print(f"    Trend   : {textwrap.fill(result.get('trend_summary',''), 80, subsequent_indent='              ')}")
    tip = result.get("tip", "")
    if tip:
        print(f"    Tip     : {tip}")
    warn = result.get("warning", "")
    if warn:
        print(f"    Warning : {_YEL}{warn}{_RST}")
    for h in result.get("highlights", []):
        direction_col = _GRN if h.get("direction") == "improving" else _RED
        print(f"      • {_BOLD}{h.get('metric','')}{_RST}: "
              f"{direction_col}{h.get('direction','')}{_RST} — {h.get('note','')}")


def _print_habits(result: dict) -> None:
    print(f"    {_cached_hint(result)}")
    print(f"    Intro   : {textwrap.fill(result.get('intro',''), 80, subsequent_indent='              ')}")
    for pick in result.get("picks", []):
        print(f"      • {_BOLD}{pick.get('label','')}{_RST} ({pick.get('category','')}/{pick.get('tier','')})")
        print(f"        {_DIM}{pick.get('why','')}{_RST}")


def _print_goal(result: dict) -> None:
    action = result.get("action", "")
    color = _GRN if action == "raise" else _YEL if action == "keep" else _RED
    print(f"    {_cached_hint(result)}")
    print(f"    Action  : {color}{_BOLD}{action.upper()}{_RST}")
    print(f"    Target  : {result.get('suggested_target','?')} steps")
    print(f"    Reason  : {textwrap.fill(result.get('reason',''), 80, subsequent_indent='              ')}")
    print(f"    Confidence: {result.get('confidence','?')}")


# ─────────────────────────────────────────────────────────────────────────────
# Runner for a single feature
# ─────────────────────────────────────────────────────────────────────────────

def _print_raw(label: str, data: dict) -> None:
    print(f"\n  {_BLU}── raw AI response [{label}]{_RST}")
    print(json.dumps(data, indent=4, ensure_ascii=False, default=str))
    print()


async def run_feature(feature: str, db, user_id: str, user, stats_only: bool, show_raw: bool = False) -> dict:
    """
    Returns {"ok": bool, "cached": bool|None, "elapsed": float, "error": str|None}
    """
    t0 = time.monotonic()
    result = {"ok": False, "cached": None, "elapsed": 0.0, "error": None}

    try:
        if feature == "insight":
            from app.services.ai_insight import _collect_stats, get_home_insight, generate_nightly_insights

            if stats_only:
                stats = await _collect_stats(db, user_id)
                print(f"\n  {_BLU}[insight stats]{_RST}")
                print(json.dumps(stats, indent=4))
                result["ok"] = True
            else:
                # get_home_insight only reads from ai_insights table (DB cache).
                # It never calls AI. The nightly scheduler job fills this table
                # at 00:30 IST using YESTERDAY's step/habit data.
                # If no row exists for today, we generate it on-demand here.
                r = await get_home_insight(db, user_id)
                if r is None:
                    print(f"    {_YEL}[NO INSIGHT in DB for today — generating now via AI...]{_RST}")
                    # generate_nightly_insights calls AI and saves to ai_insights table.
                    # Stats it collects:
                    #   steps_yesterday   = steps logged the PREVIOUS calendar day
                    #   steps_week        = sum of last 7 days including today
                    #   habits_done_yesterday = habits completed YESTERDAY
                    #   habit_pct_week    = % of habits done across last 7 days
                    #   streak_current    = global step streak from users table
                    #   rank              = previous_rank from challenge_participants
                    count = await generate_nightly_insights(db, user_id=user_id)
                    if count == 0:
                        print(f"    {_YEL}[SKIP] Already existed or no eligible data for this user.{_RST}")
                        result["ok"] = True
                        result["cached"] = None
                    else:
                        # Read back what was just saved
                        r = await get_home_insight(db, user_id)
                        if r:
                            _print_insight(r)
                            if show_raw:
                                _print_raw("insight", r)
                            result["ok"] = True
                            result["cached"] = False
                        else:
                            result["error"] = "Generated but could not read back from DB"
                else:
                    print(f"    {_DIM}[from DB cache — generated by nightly job]{_RST}")
                    _print_insight(r)
                    if show_raw:
                        _print_raw("insight", r)
                    result["ok"] = True
                    result["cached"] = True

        elif feature == "coach":
            from app.services.ai_coach import _collect_coach_stats, get_coach_report

            if stats_only:
                stats = await _collect_coach_stats(db, user_id)
                print(f"\n  {_BLU}[coach stats]{_RST}")
                print(json.dumps(stats, indent=4, default=str))
                result["ok"] = True
            else:
                r = await get_coach_report(db, user_id)
                _print_coach(r)
                if show_raw:
                    _print_raw("coach", r)
                result["ok"] = True
                result["cached"] = r.get("cached")

        elif feature == "body":
            from app.services.ai_recommendations import (
                _collect_body_stats, get_body_insight
            )
            if stats_only:
                stats = await _collect_body_stats(db, user_id)
                print(f"\n  {_BLU}[body stats]{_RST}")
                print(json.dumps(stats, indent=4, default=str))
                result["ok"] = True
            else:
                r = await get_body_insight(db, user_id)
                _print_body(r)
                if show_raw and r:
                    _print_raw("body", r)
                result["ok"] = True
                result["cached"] = r.get("cached") if r else None

        elif feature == "habits":
            from app.services.ai_recommendations import (
                _collect_habit_stats, get_habit_recommendations
            )
            if stats_only:
                stats = await _collect_habit_stats(db, user_id, user)
                print(f"\n  {_BLU}[habit stats]{_RST}")
                print(json.dumps(stats, indent=4, default=str))
                result["ok"] = True
            else:
                r = await get_habit_recommendations(db, user_id, user)
                _print_habits(r)
                if show_raw:
                    _print_raw("habits", r)
                result["ok"] = True
                result["cached"] = r.get("cached")

        elif feature == "goal":
            from app.services.ai_recommendations import (
                _collect_goal_stats, get_step_goal_suggestion
            )
            if stats_only:
                stats = await _collect_goal_stats(db, user_id)
                print(f"\n  {_BLU}[goal stats]{_RST}")
                print(json.dumps(stats, indent=4, default=str))
                result["ok"] = True
            else:
                r = await get_step_goal_suggestion(db, user_id)
                _print_goal(r)
                if show_raw:
                    _print_raw("goal", r)
                result["ok"] = True
                result["cached"] = r.get("cached")

    except Exception as exc:
        result["error"] = str(exc)
        # Roll back the aborted transaction so the session stays usable
        try:
            await db.rollback()
        except Exception:
            pass

    result["elapsed"] = time.monotonic() - t0
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Fetch users
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_all_users(db):
    from sqlalchemy import text
    rows = await db.execute(text("""
        SELECT id::text, name, email
        FROM users
        ORDER BY name
    """))
    return [{"id": r[0], "name": r[1], "email": r[2]} for r in rows.all()]


async def fetch_user(db, user_id: str):
    """Return the ORM User object (needed by habit-recommendations)."""
    from sqlalchemy import select
    from app.models import User
    row = await db.execute(select(User).where(User.id == user_id))
    return row.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(args):

    # ── optionally override provider ─────────────────────────────────────────
    if args.provider:
        os.environ["AI_PROVIDER"] = args.provider
        from importlib import reload
        import app.core.config as cfg
        reload(cfg)
        cfg.settings.__dict__["AI_PROVIDER"] = args.provider

    provider_label = os.environ.get("AI_PROVIDER", "azure")
    features = FEATURE_ORDER if args.type == "all" else [args.type]

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # ── resolve user list ─────────────────────────────────────────────
        if args.all_users:
            users_meta = await fetch_all_users(db)
            if not users_meta:
                print(f"{_RED}No users found in the database.{_RST}")
                sys.exit(1)
        elif args.user_id:
            # quick lookup to get name/email for display
            from sqlalchemy import text
            row = await db.execute(text(
                "SELECT id::text, name, email FROM users WHERE id = :uid"
            ), {"uid": args.user_id})
            rec = row.one_or_none()
            if rec is None:
                print(f"{_RED}User {args.user_id} not found.{_RST}")
                sys.exit(1)
            users_meta = [{"id": rec[0], "name": rec[1], "email": rec[2]}]
        else:
            print(f"{_RED}Specify --user-id <uuid> or --all-users.{_RST}")
            sys.exit(1)

        # ── summary table ─────────────────────────────────────────────────
        summary: list[dict] = []

        for um in users_meta:
            uid   = um["id"]
            label = f"{um.get('name') or '?':20s}  {um.get('email', '')}"

            _hdr(f"User: {label}\nID  : {uid}\nProvider: {provider_label}", "═")

            # load ORM user once per user (needed for habits)
            user_orm = await fetch_user(db, uid)
            if user_orm is None:
                print(f"  {_RED}Could not load ORM User object — skipping.{_RST}")
                continue

            user_row: dict = {"user": label, "uid": uid}

            for feat in features:
                feat_label = f"{feat.upper():8s}"
                print(f"{_BOLD}── {feat_label} ─────────────────────────────────{_RST}")

                res = await run_feature(feat, db, uid, user_orm, args.stats_only, args.raw)

                elapsed = f"{res['elapsed']:.2f}s"
                if res["ok"]:
                    cached_tag = ""
                    if res["cached"] is True:
                        cached_tag = " (cached)"
                    elif res["cached"] is False:
                        cached_tag = " (fresh)"
                    _ok(f"{feat_label} done{cached_tag}", elapsed)
                    user_row[feat] = f"OK{cached_tag}"
                else:
                    _fail(f"{feat_label} FAILED", f"{res['error']}  [{elapsed}]")
                    user_row[feat] = f"FAIL: {res['error'][:60]}"

                print()

            summary.append(user_row)

        # ── final summary table ───────────────────────────────────────────
        _hdr("SUMMARY", "═")
        col_w = 24
        feat_w = 14
        header = f"{'User':{col_w}}" + "".join(f"{f.upper():{feat_w}}" for f in features)
        print(f"  {_BOLD}{header}{_RST}")
        print("  " + "─" * (col_w + feat_w * len(features)))
        for row in summary:
            user_col = (row["user"])[:col_w - 1].ljust(col_w)
            cells = ""
            for f in features:
                val = row.get(f, "SKIP")
                col = _GRN if val.startswith("OK") else _RED
                cells += f"{col}{val[:feat_w - 1]:{feat_w}}{_RST}"
            print(f"  {user_col}{cells}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test all AI calls — single user or all users",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id",   metavar="UUID",
                        help="Run against a single user UUID")
    target.add_argument("--all-users", action="store_true",
                        help="Run against every user in the database")


    parser.add_argument(
        "--type",
        default="all",
        choices=FEATURE_ORDER + ["all"],
        help="Which AI feature to test (default: all)",
    )
    parser.add_argument(
        "--provider",
        choices=["azure", "anthropic"],
        default=None,
        help="Override the AI_PROVIDER env var",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print collected stats JSON without making any AI call",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the full raw JSON returned by the AI after each result",
    )

    args = parser.parse_args()

    # Validate UUID format early to give a clear error before hitting the DB
    if args.user_id:
        import re
        _UUID_RE = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            re.IGNORECASE
        )
        if not _UUID_RE.match(args.user_id):
            parser.error(
                f"Invalid UUID: '{args.user_id}'\n"
                f"  Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (8-4-4-4-12 hex chars)\n"
                f"  Got segment lengths: {'-'.join(str(len(p)) for p in args.user_id.split('-'))}"
            )

    asyncio.run(main(args))
