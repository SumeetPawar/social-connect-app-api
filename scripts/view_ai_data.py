"""
View all AI table data — ai_insights, ai_coach_reports, ai_recommendations.

Tables
------
  insights      — ai_insights          (one per user per day)
  coach         — ai_coach_reports     (one per user, refreshed weekly)
  body          — ai_recommendations WHERE type='body_insight'
  habits        — ai_recommendations WHERE type='habit_picks'
  goal          — ai_recommendations WHERE type='step_goal'
  all           — every table above (default)

Usage examples (from project root)
------------------------------------
  # All tables, all users
  python scripts/view_ai_data.py

  # One table
  python scripts/view_ai_data.py --table insights
  python scripts/view_ai_data.py --table coach

  # Filter by user
  python scripts/view_ai_data.py --user-id <uuid>

  # Show raw JSON payload / stats
  python scripts/view_ai_data.py --table coach --raw

  # Delete today's insight for a user (so it can be regenerated)
  python scripts/view_ai_data.py --delete-insight --user-id <uuid>

  # Delete specific AI recommendation type for a user
  python scripts/view_ai_data.py --delete-rec body --user-id <uuid>

  # Last N rows (default 10)
  python scripts/view_ai_data.py --table insights --limit 20
"""

import asyncio
import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── silence SQLAlchemy query logging ─────────────────────────────────────────
import logging as _logging
_logging.getLogger("sqlalchemy.engine").setLevel(_logging.WARNING)
_logging.getLogger("sqlalchemy.pool").setLevel(_logging.WARNING)
_logging.getLogger("app.db.session").setLevel(_logging.WARNING)
import app.db.session as _db_session
_db_session.engine.echo = False

# ── ANSI ──────────────────────────────────────────────────────────────────────
_RST   = "\033[0m"
_BOLD  = "\033[1m"
_DIM   = "\033[2m"
_GRN   = "\033[32m"
_RED   = "\033[31m"
_YEL   = "\033[33m"
_CYN   = "\033[36m"
_MAG   = "\033[35m"
_BLU   = "\033[34m"

_COLOR_MAP = {"purple": _MAG, "green": _GRN, "orange": _YEL, "rose": _RED, "teal": _CYN}

TABLE_CHOICES = ["insights", "coach", "body", "habits", "goal", "all"]
REC_TYPES     = {"body": "body_insight", "habits": "habit_picks", "goal": "step_goal"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _hdr(title: str, char: str = "─") -> None:
    print(f"\n{_BOLD}{char*64}{_RST}")
    print(f"  {_BOLD}{title}{_RST}")
    print(f"{_BOLD}{char*64}{_RST}\n")


def _age(dt: datetime) -> str:
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m = rem // 60
    if delta.days >= 1:
        return f"{delta.days}d {h%24}h ago"
    if h:
        return f"{h}h {m}m ago"
    return f"{m}m ago"


def _render_spans(spans: list) -> str:
    out = ""
    for span in (spans or []):
        pre = _COLOR_MAP.get(span.get("color", ""), "")
        if span.get("style") in ("stat", "milestone"):
            pre += _BOLD
        out += pre + span.get("text", "") + (_RST if pre else "")
    return out


def _wrap(text: str, width: int = 80, indent: int = 14) -> str:
    return textwrap.fill(text, width, subsequent_indent=" " * indent)


def _json_block(data, indent: int = 4) -> str:
    return json.dumps(data, indent=indent, default=str, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# ai_insights
# ─────────────────────────────────────────────────────────────────────────────

async def show_insights(db, user_id: str | None, limit: int, show_raw: bool) -> None:
    from sqlalchemy import text
    uid_filter = "AND i.user_id = :uid" if user_id else ""
    rows = await db.execute(text(f"""
        SELECT
            i.id,
            u.name,
            u.email,
            i.user_id::text,
            i.insight_date,
            i.provider,
            i.badge,
            i.segments,
            i.detail,
            i.hook,
            i.raw_stats,
            i.created_at
        FROM ai_insights i
        JOIN users u ON u.id = i.user_id
        WHERE 1=1 {uid_filter}
        ORDER BY i.insight_date DESC, u.name
        LIMIT :lim
    """), {"uid": user_id, "lim": limit})
    records = rows.mappings().all()

    _hdr(f"ai_insights  ({len(records)} rows shown, limit {limit})", "═")

    if not records:
        print(f"  {_YEL}No rows found.{_RST}\n")
        return

    for r in records:
        date_col = _BOLD + str(r["insight_date"]) + _RST
        age_col  = _DIM + _age(r["created_at"]) + _RST
        prov_col = (_GRN if r["provider"] == "azure" else _MAG) + r["provider"] + _RST
        print(f"  {_BOLD}#{r['id']}{_RST}  {date_col}  {age_col}  [{prov_col}]")
        print(f"  {_DIM}User : {r['name']} <{r['email']}>{_RST}")
        print(f"  Badge  : {_BOLD}[{r['badge']}]{_RST}")
        print(f"  Headline: {_render_spans(r['segments'])}")
        print(f"  Detail  : {_render_spans(r['detail'])}")
        print(f"  Hook    : {_CYN}{r['hook']}{_RST}")
        if show_raw:
            print(f"\n  {_DIM}── raw_stats{_RST}")
            print("  " + _json_block(r["raw_stats"]).replace("\n", "\n  "))
        print()


# ─────────────────────────────────────────────────────────────────────────────
# ai_coach_reports
# ─────────────────────────────────────────────────────────────────────────────

async def show_coach(db, user_id: str | None, limit: int, show_raw: bool) -> None:
    from sqlalchemy import text
    uid_filter = "AND c.user_id = :uid" if user_id else ""
    rows = await db.execute(text(f"""
        SELECT
            c.id,
            u.name,
            u.email,
            c.user_id::text,
            c.provider,
            c.summary,
            c.went_well,
            c.improve,
            c.focus,
            c.raw_stats,
            c.created_at
        FROM ai_coach_reports c
        JOIN users u ON u.id = c.user_id
        WHERE 1=1 {uid_filter}
        ORDER BY c.created_at DESC
        LIMIT :lim
    """), {"uid": user_id, "lim": limit})
    records = rows.mappings().all()

    _hdr(f"ai_coach_reports  ({len(records)} rows shown, limit {limit})", "═")

    if not records:
        print(f"  {_YEL}No rows found.{_RST}\n")
        return

    for r in records:
        prov_col = (_GRN if r["provider"] == "azure" else _MAG) + r["provider"] + _RST
        print(f"  {_BOLD}#{r['id']}{_RST}  {_DIM}{_age(r['created_at'])}{_RST}  [{prov_col}]")
        print(f"  {_DIM}User : {r['name']} <{r['email']}>{_RST}")
        print(f"  Summary : {_wrap(r['summary'] or '')}")
        print(f"  Focus   : {_BOLD}{r['focus']}{_RST}")

        ww = r["went_well"] or []
        im = r["improve"]   or []
        print(f"  Wins ({len(ww)}):")
        for w in ww:
            print(f"    {_GRN}✔{_RST} {_BOLD}{w.get('title','')}{_RST}")
            print(f"      {_DIM}{_wrap(w.get('body',''), indent=6)}{_RST}")
        print(f"  Gaps ({len(im)}):")
        for i in im:
            print(f"    {_YEL}△{_RST} {_BOLD}{i.get('title','')}{_RST}")
            print(f"      {_DIM}{_wrap(i.get('body',''), indent=6)}{_RST}")
            if i.get("suggestion"):
                print(f"      {_CYN}→ {_wrap(i['suggestion'], indent=8)}{_RST}")

        if show_raw:
            print(f"\n  {_DIM}── raw_stats{_RST}")
            print("  " + _json_block(r["raw_stats"]).replace("\n", "\n  "))
        print()


# ─────────────────────────────────────────────────────────────────────────────
# ai_recommendations
# ─────────────────────────────────────────────────────────────────────────────

async def show_recommendations(
    db, rec_type: str, label: str, user_id: str | None, limit: int, show_raw: bool
) -> None:
    from sqlalchemy import text
    uid_filter = "AND r.user_id = :uid" if user_id else ""
    rows = await db.execute(text(f"""
        SELECT
            r.id,
            u.name,
            u.email,
            r.user_id::text,
            r.type,
            r.provider,
            r.payload,
            r.raw_stats,
            r.created_at
        FROM ai_recommendations r
        JOIN users u ON u.id = r.user_id
        WHERE r.type = :rtype {uid_filter}
        ORDER BY r.created_at DESC
        LIMIT :lim
    """), {"rtype": rec_type, "uid": user_id, "lim": limit})
    records = rows.mappings().all()

    _hdr(f"ai_recommendations [{rec_type}]  ({len(records)} rows, limit {limit})", "═")

    if not records:
        print(f"  {_YEL}No rows found.{_RST}\n")
        return

    for r in records:
        prov_col = (_GRN if r["provider"] == "azure" else _MAG) + r["provider"] + _RST
        payload  = r["payload"] or {}
        print(f"  {_BOLD}#{r['id']}{_RST}  {_DIM}{_age(r['created_at'])}{_RST}  [{prov_col}]")
        print(f"  {_DIM}User : {r['name']} <{r['email']}>{_RST}")

        if rec_type == "body_insight":
            print(f"  Trend   : {_wrap(payload.get('trend_summary',''))}")
            tip  = payload.get("tip", "")
            warn = payload.get("warning", "")
            if tip:
                print(f"  Tip     : {tip}")
            if warn:
                print(f"  Warning : {_YEL}{warn}{_RST}")
            for h in payload.get("highlights", []):
                dc = _GRN if h.get("direction") == "improving" else _RED
                print(f"    • {_BOLD}{h.get('metric','')}{_RST}: "
                      f"{dc}{h.get('direction','')}{_RST} — {h.get('note','')}")

        elif rec_type == "habit_picks":
            print(f"  Intro   : {_wrap(payload.get('intro',''))}")
            for p in payload.get("picks", []):
                print(f"    • {_BOLD}{p.get('label','')}{_RST} "
                      f"({p.get('category','')}/{p.get('tier','')})")
                print(f"      {_DIM}{p.get('why','')}{_RST}")

        elif rec_type == "step_goal":
            action = payload.get("action", "")
            ac = _GRN if action == "raise" else _YEL if action == "keep" else _RED
            print(f"  Action  : {ac}{_BOLD}{action.upper()}{_RST}  "
                  f"→ {_BOLD}{payload.get('suggested_target','?')}{_RST} steps")
            print(f"  Reason  : {_wrap(payload.get('reason',''))}")
            print(f"  Confidence: {payload.get('confidence','?')}")

        if show_raw:
            print(f"\n  {_DIM}── payload{_RST}")
            print("  " + _json_block(payload).replace("\n", "\n  "))
            print(f"\n  {_DIM}── raw_stats{_RST}")
            print("  " + _json_block(r["raw_stats"]).replace("\n", "\n  "))
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Delete helpers
# ─────────────────────────────────────────────────────────────────────────────

async def delete_today_insight(db, user_id: str) -> None:
    from datetime import date
    from sqlalchemy import text
    result = await db.execute(text("""
        DELETE FROM ai_insights
        WHERE user_id = :uid AND insight_date = :today
        RETURNING id, insight_date
    """), {"uid": user_id, "today": date.today()})
    rows = result.all()
    await db.commit()
    if rows:
        print(f"  {_GRN}✔ Deleted insight #{rows[0][0]} for {rows[0][1]}{_RST}")
    else:
        print(f"  {_YEL}No insight found for today for user {user_id}{_RST}")


async def delete_recommendation(db, rec_type: str, user_id: str) -> None:
    from sqlalchemy import text
    result = await db.execute(text("""
        DELETE FROM ai_recommendations
        WHERE user_id = :uid AND type = :rtype
        RETURNING id, type, created_at
    """), {"uid": user_id, "rtype": rec_type})
    rows = result.all()
    await db.commit()
    if rows:
        for r in rows:
            print(f"  {_GRN}✔ Deleted {r[1]} #{r[0]} (created {_age(r[2])}){_RST}")
    else:
        print(f"  {_YEL}No {rec_type} recommendation found for user {user_id}{_RST}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary counts
# ─────────────────────────────────────────────────────────────────────────────

async def show_counts(db) -> None:
    from sqlalchemy import text
    rows = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM ai_insights)                                         AS insights_total,
            (SELECT COUNT(*) FROM ai_insights WHERE insight_date = CURRENT_DATE)       AS insights_today,
            (SELECT COUNT(*) FROM ai_coach_reports)                                    AS coach_total,
            (SELECT COUNT(DISTINCT user_id) FROM ai_coach_reports)                     AS coach_users,
            (SELECT COUNT(*) FROM ai_recommendations WHERE type = 'body_insight')      AS body_total,
            (SELECT COUNT(*) FROM ai_recommendations WHERE type = 'habit_picks')       AS habits_total,
            (SELECT COUNT(*) FROM ai_recommendations WHERE type = 'step_goal')         AS goal_total,
            (SELECT COUNT(DISTINCT id) FROM users)                                     AS total_users
    """))
    c = rows.mappings().first()
    _hdr("AI Table Counts", "━")
    print(f"  {'Total users':<30} {c['total_users']}")
    print()
    print(f"  {'ai_insights (total)':<30} {c['insights_total']}")
    print(f"  {'ai_insights (today)':<30} {_GRN}{c['insights_today']}{_RST}")
    print()
    print(f"  {'ai_coach_reports (total)':<30} {c['coach_total']}")
    print(f"  {'ai_coach_reports (users)':<30} {c['coach_users']}")
    print()
    print(f"  {'ai_recommendations body':<30} {c['body_total']}")
    print(f"  {'ai_recommendations habits':<30} {c['habits_total']}")
    print(f"  {'ai_recommendations goal':<30} {c['goal_total']}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(args):
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:

        # ── delete modes ──────────────────────────────────────────────────────
        if args.delete_insight:
            if not args.user_id:
                print(f"{_RED}--user-id is required with --delete-insight{_RST}")
                sys.exit(1)
            _hdr("Delete today's insight")
            await delete_today_insight(db, args.user_id)
            return

        if args.delete_rec:
            if not args.user_id:
                print(f"{_RED}--user-id is required with --delete-rec{_RST}")
                sys.exit(1)
            _hdr(f"Delete recommendation [{args.delete_rec}]")
            await delete_recommendation(db, REC_TYPES[args.delete_rec], args.user_id)
            return

        # ── view mode ─────────────────────────────────────────────────────────
        await show_counts(db)

        tables = (
            ["insights", "coach", "body", "habits", "goal"]
            if args.table == "all"
            else [args.table]
        )

        for t in tables:
            if t == "insights":
                await show_insights(db, args.user_id, args.limit, args.raw)
            elif t == "coach":
                await show_coach(db, args.user_id, args.limit, args.raw)
            elif t in REC_TYPES:
                await show_recommendations(
                    db, REC_TYPES[t], t, args.user_id, args.limit, args.raw
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="View / manage AI table data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--table", "-t",
        default="all",
        choices=TABLE_CHOICES,
        help="Which table to display (default: all)",
    )
    parser.add_argument(
        "--user-id", "-u",
        metavar="UUID",
        default=None,
        help="Filter by user UUID",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="Max rows per table (default: 10)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Also print raw JSON payload / raw_stats for each row",
    )
    parser.add_argument(
        "--delete-insight",
        action="store_true",
        help="Delete today's insight for --user-id (so it can be regenerated)",
    )
    parser.add_argument(
        "--delete-rec",
        choices=list(REC_TYPES.keys()),
        metavar="TYPE",
        default=None,
        help="Delete cached recommendation of TYPE (body/habits/goal) for --user-id",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
