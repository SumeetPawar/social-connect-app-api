"""
AI Insight service — generates a daily personalised summary for the home screen.

Output format (rich text):
    {
      "badge":    str,          # short upbeat label (plain text)
      "segments": [...],        # headline as rich-text spans
      "detail":   [...],        # detail line as rich-text spans
      "hook":     str,          # "come back tomorrow" engagement line (plain text)
    }

Each span in segments / detail:
    {
      "text":  str,
      "style": "normal" | "stat" | "highlight" | "milestone",
      "color": "purple" | "green" | "orange" | "rose" | "teal" | null
    }

Frontend rendering guide:
  normal    → body text, no decoration
  stat      → bold, accent color — use for numbers (steps, habit counts)
  highlight → coloured pill / chip background — use for labels / habit names
  milestone → bold + larger, gold/orange — use for streak or rank achievements

Cache:
  One row in ai_insights per user per day.
  Re-uses cached result on repeat calls; regenerates the next calendar day.

Provider:
  Controlled by AI_PROVIDER env var: "anthropic" (default) | "azure"
"""
import logging
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AiInsight

logger = logging.getLogger(__name__)

# ── lazy provider clients ─────────────────────────────────────────────────────

_anthropic_client = None
_azure_client = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.AsyncAnthropic()
    return _anthropic_client


def _get_azure():
    global _azure_client
    if _azure_client is None:
        from openai import AsyncAzureOpenAI
        _azure_client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return _azure_client


# ── public entry point ────────────────────────────────────────────────────────

async def get_home_insight(db: AsyncSession, user_id: str) -> dict:
    """
    Return today's insight for the user.
    Hits the DB cache first; calls the AI only if no entry exists for today.
    """
    today = date.today()
    provider = settings.AI_PROVIDER.lower()

    # Cache lookup
    cached = await db.execute(
        select(AiInsight).where(
            AiInsight.user_id == user_id,
            AiInsight.insight_date == today,
        )
    )
    row = cached.scalar_one_or_none()
    if row:
        return {
            "badge":    row.badge,
            "segments": row.segments,
            "detail":   row.detail,
            "hook":     row.hook,
        }

    # Generate fresh
    stats = await _collect_stats(db, user_id)
    insight = await _call_provider(stats, provider)

    # Persist (upsert — safe if two requests race)
    try:
        db.add(AiInsight(
            user_id=user_id,
            insight_date=today,
            provider=provider,
            badge=insight["badge"],
            segments=insight["segments"],
            detail=insight["detail"],
            hook=insight["hook"],
            raw_stats=stats,
        ))
        await db.commit()
    except Exception as e:
        # Duplicate key from concurrent request — ignore, still return insight
        await db.rollback()
        logger.debug(f"AI insight upsert skipped (likely race): {e}")

    return insight


# ── data collection ───────────────────────────────────────────────────────────

async def _collect_stats(db: AsyncSession, user_id: str) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)

    steps_row = await db.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN day = :yesterday THEN steps ELSE 0 END), 0) AS steps_yesterday,
            COALESCE(SUM(CASE WHEN day >= :week_start THEN steps ELSE 0 END), 0) AS steps_week,
            COALESCE(ROUND(AVG(CASE WHEN day >= :week_start THEN steps END)), 0) AS steps_avg
        FROM daily_steps
        WHERE user_id = :uid
    """), {"uid": str(user_id), "yesterday": yesterday, "week_start": week_start})
    s = steps_row.mappings().first() or {}

    habit_row = await db.execute(text("""
        SELECT
            COUNT(DISTINCT hcm.id) AS total_habits,
            COALESCE(SUM(CASE WHEN dl.logged_date = :yesterday AND dl.completed THEN 1 ELSE 0 END), 0) AS done_yesterday,
            COALESCE(SUM(CASE WHEN dl.logged_date >= :week_start AND dl.completed THEN 1 ELSE 0 END), 0) AS done_week,
            hc.id AS challenge_id,
            hc.started_at,
            hc.ends_at
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hc.id
        LIMIT 1
    """), {"uid": str(user_id), "yesterday": yesterday, "week_start": week_start})
    h = habit_row.mappings().first() or {}

    total = int(h.get("total_habits") or 0)
    done_yest = int(h.get("done_yesterday") or 0)
    done_week = int(h.get("done_week") or 0)
    possible_week = total * 7
    habit_pct_week = round(done_week / possible_week * 100) if possible_week else 0
    day_number = (today - h["started_at"]).days + 1 if h.get("started_at") else None
    days_remaining = (h["ends_at"] - today).days if h.get("ends_at") else None

    streak_row = await db.execute(text(
        "SELECT global_current_streak, global_longest_streak FROM users WHERE id = :uid"
    ), {"uid": str(user_id)})
    sr = streak_row.mappings().first() or {}
    streak = int(sr.get("global_current_streak") or 0)
    longest = int(sr.get("global_longest_streak") or 0)

    rank_row = await db.execute(text("""
        SELECT cp.previous_rank, cp.challenge_current_streak, cp.selected_daily_target
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = :uid AND c.status = 'active' AND cp.left_at IS NULL
        ORDER BY c.end_date DESC LIMIT 1
    """), {"uid": str(user_id)})
    cr = rank_row.mappings().first() or {}
    rank = int(cr["previous_rank"]) if cr.get("previous_rank") else None
    step_streak = int(cr.get("challenge_current_streak") or 0)
    daily_target = int(cr.get("selected_daily_target") or 8000)
    steps_vs_target_pct = round(
        int(s.get("steps_yesterday") or 0) / daily_target * 100
    ) if daily_target else 0

    worst_row = await db.execute(text("""
        SELECT hb.label,
               COUNT(dl.id) FILTER (WHERE dl.completed AND dl.logged_date >= :week_start) AS done_days
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hb.label ORDER BY done_days ASC LIMIT 1
    """), {"uid": str(user_id), "week_start": week_start})
    worst = worst_row.mappings().first()

    best_row = await db.execute(text("""
        SELECT hb.label,
               COUNT(dl.id) FILTER (WHERE dl.completed AND dl.logged_date >= :week_start) AS done_days
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hb.label ORDER BY done_days DESC LIMIT 1
    """), {"uid": str(user_id), "week_start": week_start})
    best = best_row.mappings().first()

    perfect_days_row = await db.execute(text("""
        SELECT COUNT(*) AS perfect_days
        FROM (
            SELECT dl.logged_date
            FROM habit_challenges hc
            JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
            JOIN daily_logs dl ON dl.commitment_id = hcm.id
            WHERE hc.user_id = :uid AND hc.status = 'active'
              AND dl.logged_date >= :week_start AND dl.completed
            GROUP BY dl.logged_date
            HAVING COUNT(*) = (
                SELECT COUNT(*) FROM habit_commitments hcm2
                WHERE hcm2.challenge_id = hc.id
            )
        ) t
    """), {"uid": str(user_id), "week_start": week_start})
    perfect_days = int(perfect_days_row.scalar() or 0)

    return {
        "steps_yesterday":       int(s.get("steps_yesterday") or 0),
        "steps_week":            int(s.get("steps_week") or 0),
        "steps_avg_daily":       int(s.get("steps_avg") or 0),
        "steps_daily_target":    daily_target,
        "steps_vs_target_pct":   steps_vs_target_pct,
        "habits_total":          total,
        "habits_done_yesterday": done_yest,
        "habits_done_week":      done_week,
        "habit_pct_week":        habit_pct_week,
        "habit_perfect_days_week": perfect_days,
        "habit_day_number":      day_number,
        "habit_days_remaining":  days_remaining,
        "best_habit_this_week":  best["label"] if best else None,
        "weakest_habit":         worst["label"] if worst else None,
        "weakest_habit_days_done": int(worst["done_days"]) if worst else None,
        "streak_current":        streak,
        "streak_longest":        longest,
        "step_streak":           step_streak,
        "rank":                  rank,
    }


# ── shared prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are the motivational voice inside a fitness app — sharp, warm, and honest.
Your job: turn raw 7-day stats into a daily insight that makes the user feel seen
and genuinely excited to open the app again tomorrow.

Return ONLY a valid JSON object (no markdown, no code fences) with these four keys:

"badge"
  A 2-5 word label that captures the user's mood or achievement.
  Make it feel personal and earned, not generic.
  Examples: "Streak on fire", "Comeback week", "Almost perfect", "Consistency king"

"segments"
  The headline — an array of rich-text spans that together form ONE punchy sentence
  (max 20 words total). Each span is an object:
    { "text": "...", "style": "...", "color": "..." }

  Styles:
    "normal"    — plain body text
    "stat"      — a number or metric (bold, accent color)
    "highlight" — a label or achievement (colored chip/pill background)
    "milestone" — a streak or rank worth celebrating (bold, larger, gold)

  Colors (use only these, or null for normal):
    "purple"  — steps, activity
    "green"   — completed habits, streaks
    "orange"  — streaks, fire moments
    "rose"    — body / personal bests
    "teal"    — mindfulness, calm achievements

  Rules:
  - At least 2 spans must be non-normal style
  - Lead with the most impressive or surprising number
  - If a streak milestone (7, 14, 21, 30 days) is present, use "milestone" style
  - Never start with "You"

"detail"
  A second insight as rich-text spans (max 25 words).
  - Must name a specific habit OR a time-of-day pattern
  - Should feel like a coach noticing something the user missed
  - If there is a gap (missed habit, low step day), mention it with a gentle forward-looking tip
  - Use "highlight" style for habit names, "stat" for numbers

"hook"
  One short sentence (max 12 words) that creates genuine curiosity or stakes for tomorrow.
  Tease something: a possible streak milestone, a rank they could reach, a perfect day within reach.
  Never generic ("Keep it up!"). Be specific to the data.
  Examples:
    "Two more days and the 7-day milestone is yours."
    "One step away from cracking the top 3."
    "A perfect habit day tomorrow ends the week on a high."
"""


def _build_user_message(stats: dict) -> str:
    return (
        "Here are the user's stats for the past 7 days:\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        "Generate the insight JSON now."
    )


def _validate_spans(spans: list) -> list:
    """Ensure every span has the required keys; strip unknown ones."""
    valid_styles = {"normal", "stat", "highlight", "milestone"}
    valid_colors = {"purple", "green", "orange", "rose", "teal", None}
    result = []
    for span in spans:
        if not isinstance(span, dict) or "text" not in span:
            continue
        result.append({
            "text":  str(span.get("text", "")),
            "style": span.get("style", "normal") if span.get("style") in valid_styles else "normal",
            "color": span.get("color") if span.get("color") in valid_colors else None,
        })
    return result or [{"text": "", "style": "normal", "color": None}]


def _parse_response(raw: str, stats: dict) -> dict:
    try:
        data = json.loads(raw)
        return {
            "badge":    str(data.get("badge", "")),
            "segments": _validate_spans(data.get("segments", [])),
            "detail":   _validate_spans(data.get("detail", [])),
            "hook":     str(data.get("hook", "")),
        }
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"AI insight parse error: {e} — raw={raw!r}")
        return _fallback(stats)


# ── provider dispatch ─────────────────────────────────────────────────────────

async def _call_provider(stats: dict, provider: str) -> dict:
    if provider == "azure":
        return await _ask_azure(stats)
    return await _ask_claude(stats)


async def _ask_claude(stats: dict) -> dict:
    import anthropic
    try:
        client = _get_anthropic()
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=1024,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_user_message(stats)}],
        ) as stream:
            message = await stream.get_final_message()

        raw = next((b.text for b in message.content if b.type == "text"), "{}")
        return _parse_response(raw, stats)

    except anthropic.APIError as e:
        logger.error(f"Claude insight error: {e}")
        return _fallback(stats)


async def _ask_azure(stats: dict) -> dict:
    try:
        client = _get_azure()
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": _build_user_message(stats)},
            ],
            max_tokens=1024,
            temperature=0.5,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        return _parse_response(raw, stats)

    except Exception as e:
        logger.error(f"Azure insight error: {e}")
        return _fallback(stats)


# ── fallback ──────────────────────────────────────────────────────────────────

def _fallback(stats: dict) -> dict:
    steps = stats.get("steps_yesterday", 0)
    done  = stats.get("habits_done_yesterday", 0)
    total = stats.get("habits_total", 0)
    streak = stats.get("streak_current", 0)
    return {
        "badge": "Yesterday at a glance",
        "segments": [
            {"text": f"{steps:,} steps", "style": "stat",   "color": "purple"},
            {"text": " logged and ",     "style": "normal",  "color": None},
            {"text": f"{done} of {total} habits", "style": "highlight", "color": "green"},
            {"text": " kept.",           "style": "normal",  "color": None},
        ],
        "detail": [
            {"text": "Streak at ", "style": "normal",  "color": None},
            {"text": f"{streak} days", "style": "stat", "color": "orange"},
            {"text": " — keep going.", "style": "normal", "color": None},
        ],
        "hook": "Come back tomorrow to see your streak grow.",
    }
