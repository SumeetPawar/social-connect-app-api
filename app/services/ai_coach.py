"""
AI Coach service — generates a 30-day personal coaching report.

Output format:
  {
    "summary":   str,           # 2-3 sentence plain-text coach intro
    "went_well": [              # what the user is doing well
      {"title": str, "body": str},
      ...
    ],
    "improve": [                # gaps with concrete actionable suggestions
      {"title": str, "body": str, "suggestion": str},
      ...
    ],
    "focus": str,               # single most impactful next action (1 sentence)
    "generated_at": str,        # ISO timestamp
    "cached": bool,             # True if served from DB cache
  }

Cache: one report per user, re-generated only if last report is older than 7 days.
Provider: controlled by AI_PROVIDER env var: "azure" (default) | "anthropic"
"""
import json
import logging
from datetime import datetime, date, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AiCoachReport

logger = logging.getLogger(__name__)

_CACHE_DAYS = 7   # regenerate after this many days


# ── system prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a personal fitness and habits coach reviewing a user's last 30 days of data.
Your tone: warm, direct, and honest — like a coach who genuinely cares.
Use plain language. No jargon. Short sentences. Speak to the user as "you".

Return ONLY a valid JSON object (no markdown, no code fences) with exactly these keys:

"summary"
  2-3 sentences. Acknowledge the overall effort. Mention the single best thing
  they did this month and one honest observation. Keep it human and specific.

"went_well"
  Array of 2-4 objects: {"title": str, "body": str}
  Each entry celebrates something real from the data.
  title: short label (3-6 words)
  body: 1-2 sentences, mention specific numbers where possible.
  Rules:
  - Only include genuine wins — don't manufacture positives
  - Lead with the strongest win
  - Examples: step streaks, habit completion %, perfect days, hitting daily target

"improve"
  Array of 2-3 objects: {"title": str, "body": str, "suggestion": str}
  Each entry identifies a real gap.
  title: short label (3-6 words)
  body: 1-2 sentences describing the gap with specific numbers.
  suggestion: one concrete, specific action the user can take starting tomorrow.
  Rules:
  - Never shame or blame — frame as an opportunity
  - suggestion must be specific and immediately actionable
    Good: "Log your steps before 9 PM every day this week"
    Bad:  "Try to be more consistent"
  - If a habit was consistently missed, name it by name
  - If steps were below target most days, state exactly how many more steps/day

"focus"
  One sentence (max 15 words). The single most impactful thing to do this week.
  Must be specific to the data — never generic.
  Example: "Close your step goal 5 out of 7 days this week."
"""


def _build_user_message(stats: dict) -> str:
    return (
        "Here is the user's activity data for the past 30 days:\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        "Generate the coaching report JSON now."
    )


# ── data collection ───────────────────────────────────────────────────────────

async def _collect_coach_stats(db: AsyncSession, user_id: str) -> dict:
    today = date.today()
    start_30 = today - timedelta(days=29)
    start_7  = today - timedelta(days=6)

    # ── Steps: 30-day overview ────────────────────────────────────────────────
    steps_row = await db.execute(text("""
        SELECT
            COALESCE(SUM(steps), 0)                                        AS total_steps,
            COALESCE(ROUND(AVG(steps)), 0)                                 AS avg_daily_steps,
            COUNT(*)                                                        AS days_logged,
            MAX(steps)                                                      AS best_day_steps,
            COALESCE(SUM(CASE WHEN steps > 0 THEN 1 ELSE 0 END), 0)       AS active_days
        FROM daily_steps
        WHERE user_id = :uid AND day >= :start AND day <= :today
    """), {"uid": user_id, "start": start_30, "today": today})
    s = steps_row.mappings().first() or {}

    # Days target was hit (30-day)
    target_row = await db.execute(text("""
        SELECT
            COALESCE(cp.selected_daily_target, 8000) AS daily_target,
            COUNT(ds.day) FILTER (
                WHERE ds.steps >= COALESCE(cp.selected_daily_target, 8000)
            ) AS days_target_hit
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        LEFT JOIN daily_steps ds
            ON ds.user_id = cp.user_id
            AND ds.day >= :start AND ds.day <= :today
        WHERE cp.user_id = :uid AND c.status = 'active' AND cp.left_at IS NULL
        ORDER BY c.end_date DESC
        LIMIT 1
    """), {"uid": user_id, "start": start_30, "today": today})
    t = target_row.mappings().first() or {}
    daily_target   = int(t.get("daily_target") or 8000)
    days_target_hit = int(t.get("days_target_hit") or 0)

    # Steps last 7 days
    steps_7_row = await db.execute(text("""
        SELECT COALESCE(ROUND(AVG(steps)), 0) AS avg_7
        FROM daily_steps
        WHERE user_id = :uid AND day >= :start7 AND day <= :today
    """), {"uid": user_id, "start7": start_7, "today": today})
    avg_7 = int(steps_7_row.scalar() or 0)

    # ── Habits: 30-day overview ───────────────────────────────────────────────
    habit_row = await db.execute(text("""
        SELECT
            COUNT(DISTINCT hcm.id)                                              AS total_habits,
            COALESCE(SUM(CASE WHEN dl.completed THEN 1 ELSE 0 END), 0)         AS done_30,
            COALESCE(SUM(CASE WHEN dl.logged_date >= :start7
                              AND dl.completed THEN 1 ELSE 0 END), 0)          AS done_7,
            hc.started_at,
            hc.ends_at
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
            AND dl.logged_date >= :start AND dl.logged_date <= :today
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hc.id
        LIMIT 1
    """), {"uid": user_id, "start": start_30, "start7": start_7, "today": today})
    h = habit_row.mappings().first() or {}
    total_habits = int(h.get("total_habits") or 0)
    done_30      = int(h.get("done_30") or 0)
    done_7       = int(h.get("done_7") or 0)
    possible_30  = total_habits * 30
    possible_7   = total_habits * 7
    habit_pct_30 = round(done_30 / possible_30 * 100) if possible_30 else 0
    habit_pct_7  = round(done_7  / possible_7  * 100) if possible_7  else 0

    # Perfect days (all habits done) in last 30 days
    perfect_row = await db.execute(text("""
        SELECT COUNT(*) AS perfect_days
        FROM (
            SELECT dl.logged_date
            FROM habit_challenges hc
            JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
            JOIN daily_logs dl ON dl.commitment_id = hcm.id
            WHERE hc.user_id = :uid AND hc.status = 'active'
              AND dl.logged_date >= :start AND dl.logged_date <= :today
              AND dl.completed
            GROUP BY dl.logged_date
            HAVING COUNT(*) >= :total_habits
        ) t
    """), {"uid": user_id, "start": start_30, "today": today,
           "total_habits": max(total_habits, 1)})
    perfect_days = int(perfect_row.scalar() or 0)

    # Per-habit completion breakdown
    habit_breakdown_row = await db.execute(text("""
        SELECT
            hb.label,
            COUNT(dl.id) FILTER (WHERE dl.completed AND dl.logged_date >= :start) AS done_count,
            30 AS possible
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hb.label
        ORDER BY done_count DESC
    """), {"uid": user_id, "start": start_30})
    habit_breakdown = [
        {"habit": r["label"], "done": int(r["done_count"]), "possible": 30,
         "pct": round(int(r["done_count"]) / 30 * 100)}
        for r in habit_breakdown_row.mappings().all()
    ]

    # ── Streaks ───────────────────────────────────────────────────────────────
    streak_row = await db.execute(text(
        "SELECT global_current_streak, global_longest_streak FROM users WHERE id = :uid"
    ), {"uid": user_id})
    sr = streak_row.mappings().first() or {}

    # ── Rank ─────────────────────────────────────────────────────────────────
    rank_row = await db.execute(text("""
        SELECT cp.previous_rank, cp.challenge_current_streak, cp.selected_daily_target
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = :uid AND c.status = 'active' AND cp.left_at IS NULL
        ORDER BY c.end_date DESC LIMIT 1
    """), {"uid": user_id})
    cr = rank_row.mappings().first() or {}

    return {
        # Steps
        "steps_total_30d":       int(s.get("total_steps") or 0),
        "steps_avg_daily_30d":   int(s.get("avg_daily_steps") or 0),
        "steps_avg_daily_7d":    avg_7,
        "steps_best_day":        int(s.get("best_day_steps") or 0),
        "steps_active_days":     int(s.get("active_days") or 0),
        "steps_days_logged":     int(s.get("days_logged") or 0),
        "steps_daily_target":    daily_target,
        "steps_days_target_hit": days_target_hit,
        "steps_target_hit_pct":  round(days_target_hit / 30 * 100),
        # Habits
        "habits_total":          total_habits,
        "habits_pct_30d":        habit_pct_30,
        "habits_pct_7d":         habit_pct_7,
        "habits_perfect_days":   perfect_days,
        "habit_breakdown":       habit_breakdown,
        # Streaks
        "streak_current":        int(sr.get("global_current_streak") or 0),
        "streak_longest":        int(sr.get("global_longest_streak") or 0),
        "step_streak":           int(cr.get("challenge_current_streak") or 0),
        # Rank
        "rank":                  int(cr["previous_rank"]) if cr.get("previous_rank") else None,
    }


# ── response parsing ──────────────────────────────────────────────────────────

def _validate_went_well(items: list) -> list:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append({
            "title": str(item.get("title", "")),
            "body":  str(item.get("body", "")),
        })
    return result


def _validate_improve(items: list) -> list:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append({
            "title":      str(item.get("title", "")),
            "body":       str(item.get("body", "")),
            "suggestion": str(item.get("suggestion", "")),
        })
    return result


def _parse_coach_response(raw: str, stats: dict) -> dict:
    try:
        data = json.loads(raw)
        return {
            "summary":   str(data.get("summary", "")),
            "went_well": _validate_went_well(data.get("went_well", [])),
            "improve":   _validate_improve(data.get("improve", [])),
            "focus":     str(data.get("focus", "")),
        }
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Coach report parse error: {e} — raw={raw!r}")
        return _fallback_report(stats)


def _fallback_report(stats: dict) -> dict:
    avg = stats.get("steps_avg_daily_30d", 0)
    pct = stats.get("habits_pct_30d", 0)
    target = stats.get("steps_daily_target", 8000)
    gap = max(target - avg, 0)
    return {
        "summary": (
            f"You averaged {avg:,} steps a day and completed {pct}% of your habits "
            f"over the last 30 days. Here's what the data shows."
        ),
        "went_well": [
            {"title": "Showing up consistently",
             "body": f"You logged steps on {stats.get('steps_active_days', 0)} of the last 30 days. That's a habit in itself."},
        ],
        "improve": [
            {"title": "Closing the step gap",
             "body": f"Your daily average of {avg:,} is {gap:,} steps short of your {target:,} target.",
             "suggestion": f"Add one 15-minute walk per day — that's roughly {gap:,} extra steps."},
        ],
        "focus": f"Hit your {target:,} step goal at least 5 days this week.",
    }


# ── provider calls ────────────────────────────────────────────────────────────

async def _ask_azure_coach(stats: dict) -> dict:
    from openai import AsyncAzureOpenAI
    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM + "\nRespond in valid JSON."},
                {"role": "user",   "content": _build_user_message(stats)},
            ],
            max_completion_tokens=1500,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        return _parse_coach_response(raw, stats)
    except Exception as e:
        logger.error(f"Azure coach error: {e}")
        return _fallback_report(stats)


async def _ask_claude_coach(stats: dict) -> dict:
    import anthropic
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=1500,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_user_message(stats)}],
        ) as stream:
            message = await stream.get_final_message()
        raw = next((b.text for b in message.content if b.type == "text"), "{}")
        return _parse_coach_response(raw, stats)
    except anthropic.APIError as e:
        logger.error(f"Claude coach error: {e}")
        return _fallback_report(stats)


# ── public entry point ────────────────────────────────────────────────────────

async def get_coach_report(db: AsyncSession, user_id: str) -> dict:
    """
    Return the user's coaching report.
    Serves from DB cache if last report is < 7 days old.
    Generates fresh otherwise (on-demand, not scheduled).
    """
    # Check cache
    cached = await db.execute(
        select(AiCoachReport)
        .where(AiCoachReport.user_id == user_id)
        .order_by(AiCoachReport.created_at.desc())
        .limit(1)
    )
    row = cached.scalar_one_or_none()
    if row:
        age_days = (datetime.now(timezone.utc) - row.created_at).days
        if age_days < _CACHE_DAYS:
            return {
                "summary":      row.summary,
                "went_well":    row.went_well,
                "improve":      row.improve,
                "focus":        row.focus,
                "generated_at": row.created_at.isoformat(),
                "cached":       True,
            }

    # Generate fresh
    stats = await _collect_coach_stats(db, user_id)
    provider = settings.AI_PROVIDER.lower()

    if provider == "azure":
        report = await _ask_azure_coach(stats)
    else:
        report = await _ask_claude_coach(stats)

    # Persist
    try:
        db.add(AiCoachReport(
            user_id=user_id,
            provider=provider,
            summary=report["summary"],
            went_well=report["went_well"],
            improve=report["improve"],
            focus=report["focus"],
            raw_stats=stats,
        ))
        await db.commit()
        generated_at = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        await db.rollback()
        logger.error(f"Coach report save error: {e}")
        generated_at = datetime.now(timezone.utc).isoformat()

    return {
        "summary":      report["summary"],
        "went_well":    report["went_well"],
        "improve":      report["improve"],
        "focus":        report["focus"],
        "generated_at": generated_at,
        "cached":       False,
    }
