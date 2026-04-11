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
from app.services.habits_service import get_streak as _get_habit_streak

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
        try:
            from openai import AsyncAzureOpenAI
        except ModuleNotFoundError:
            raise ModuleNotFoundError(
                "The 'openai' package is not installed in the active Python environment. "
                "Run: .venv311\\Scripts\\python.exe -m pip install openai"
            )
        _azure_client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return _azure_client


# ── public entry point ────────────────────────────────────────────────────────

async def get_home_insight(db: AsyncSession, user_id: str) -> dict | None:
    """
    Return today's pre-generated insight for the user, or None if not ready yet.
    Insights are generated nightly by the scheduler — never on demand here.
    """
    today = date.today()
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
    return None


async def generate_nightly_insights(db: AsyncSession, user_id: str | None = None) -> int:
    """
    Nightly scheduled job — runs after midnight (00:30 IST).
    Generates a daily insight for every user who has push subscriptions or
    active habit/step data. Stores insight_date = today (the new day),
    stats collected from yesterday.
    Skips users who already have a row for today (safe to re-run).

    Pass user_id to generate for a single specific user (useful for testing).
    Returns count of insights generated.
    """
    from sqlalchemy import text as _text
    today = date.today()
    provider = settings.AI_PROVIDER.lower()
    generated = 0

    if user_id:
        # Single-user mode
        user_ids = [str(user_id)]
        logger.info(f"Nightly insight job: single-user mode for {user_id}")
    else:
        # All distinct users who have any recent activity or subscriptions
        users_row = await db.execute(_text("""
            SELECT DISTINCT u.id
            FROM users u
            WHERE EXISTS (
                SELECT 1 FROM daily_steps ds
                WHERE ds.user_id = u.id AND ds.day >= :since
            ) OR EXISTS (
                SELECT 1 FROM habit_challenges hc
                WHERE hc.user_id = u.id AND hc.status = 'active'
            ) OR EXISTS (
                SELECT 1 FROM push_subscriptions ps
                WHERE ps.user_id = u.id
            )
        """), {"since": today - timedelta(days=14)})
        user_ids = [str(r[0]) for r in users_row.all()]
        logger.info(f"Nightly insight job: generating for {len(user_ids)} users")

    for uid in user_ids:
        try:
            # Skip if already generated today
            existing = await db.execute(
                select(AiInsight).where(
                    AiInsight.user_id == uid,
                    AiInsight.insight_date == today,
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"[insight] {uid}: already exists today — skipping")
                continue

            # Collect stats
            try:
                stats = await _collect_stats(db, uid)
            except Exception as e:
                logger.error(f"[insight] {uid}: _collect_stats failed — {e}", exc_info=True)
                await db.rollback()
                continue

            # Skip users with no meaningful data — saves AI quota, avoids junk insight
            has_data = (
                stats.get("steps_yesterday", 0) > 0
                or stats.get("steps_week", 0) > 0
                or stats.get("habits_total", 0) > 0
            )
            if not has_data:
                logger.info(f"[insight] {uid}: no activity data — skipping")
                continue

            # Call AI (falls back internally on provider errors)
            try:
                insight = await _call_provider(stats, provider)
            except Exception as e:
                logger.error(f"[insight] {uid}: _call_provider failed — {e}", exc_info=True)
                insight = _fallback(stats)

            db.add(AiInsight(
                user_id=uid,
                insight_date=today,
                provider=provider,
                badge=insight["badge"],
                segments=insight["segments"],
                detail=insight["detail"],
                hook=insight["hook"],
                raw_stats=stats,
            ))
            await db.commit()
            generated += 1
            logger.info(f"[insight] {uid}: generated ok (provider={provider})")

        except Exception as e:
            await db.rollback()
            logger.error(f"[insight] {uid}: unexpected error — {e}", exc_info=True)

    logger.info(f"Nightly insight job: generated {generated} new insights")
    return generated


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

    # ── Step challenge: rank + step streak ────────────────────────────────
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

    # ── Habit streak: live effective streak from shield logic ───────────────
    habit_challenge_id = int(h["challenge_id"]) if h.get("challenge_id") else None
    habit_effective_streak = 0
    habit_longest_streak   = 0
    habit_raw_streak       = 0
    if habit_challenge_id:
        try:
            sd = await _get_habit_streak(db, habit_challenge_id, str(user_id))
            habit_effective_streak = sd.get("effective_streak", 0)
            habit_longest_streak   = sd.get("longest_streak", 0)
            habit_raw_streak       = sd.get("current_streak", 0)
        except Exception:
            pass
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
            HAVING COUNT(*) >= :total_habits
        ) t
    """), {"uid": str(user_id), "week_start": week_start, "total_habits": max(total, 1)})
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
        "step_streak":              step_streak,
        "step_rank":                rank,
        "habit_streak_current":     habit_raw_streak,
        "habit_streak_effective":   habit_effective_streak,  # shield-protected (use this for display)
        "habit_streak_longest":     habit_longest_streak,
    }


# ── shared prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are the motivational voice inside a fitness app — sharp, warm, and honest.
Your job: turn raw 7-day stats into a daily insight that makes the user feel seen
and genuinely excited to open the app again tomorrow.

IMPORTANT: The user message will tell you whether this user tracks steps, habits, or both.
Only reference data that exists for them. Never mention steps for a habits-only user,
never mention habits for a steps-only user. The insight should feel like it was written
specifically for their situation, not a generic template.

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

  SPACING RULE: spans are concatenated directly by the frontend.
  Every "normal" span that sits between two styled spans MUST start and end
  with a space so words don't run together.
  Example: [{"text":"Rank ","style":"stat",...},{"text":" this week with ","style":"normal",...},{"text":"8,000","style":"stat",...}]

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
  One short, scannable fact (max 12 words total across all spans).
  Pick the SINGLE most useful number the user can act on today:
    • Best option: habit completion rate  e.g. "Exercise 30 min — done 5 of 7 days"
    • Or: steps vs target         e.g. "Averaged 6,200 steps — 78% of your daily target"
    • Or: streak status           e.g. "Step streak: 4 days — keep it going"
    • Or: perfect days count      e.g. "2 perfect habit days this week"
  Rules:
  - Use "highlight" style for habit names, "stat" for all numbers
  - NO coaching advice, NO tips, NO forward-looking sentences — just the fact
  - Must be readable in under 2 seconds

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
    has_steps  = stats.get("steps_week", 0) > 0 or stats.get("steps_yesterday", 0) > 0
    has_habits = stats.get("habits_total", 0) > 0
    step_rank  = stats.get("step_rank")
    step_streak = stats.get("step_streak", 0)
    habit_streak = stats.get("habit_streak_effective", 0)
    habit_pct = stats.get("habit_pct_week", 0)

    if has_steps and has_habits:
        # Guide the AI to lead with the most impressive metric
        if step_rank and step_rank <= 3:
            lead = f"Their step ranking is impressive: #{step_rank} on the leaderboard."
        elif habit_streak >= 7:
            lead = f"Their habit streak is strong: {habit_streak} days effective."
        elif step_streak >= 5:
            lead = f"They are on a {step_streak}-day step streak."
        elif habit_pct >= 80:
            lead = f"They completed {habit_pct}% of habits this week."
        else:
            lead = "Use the most interesting number from the stats."
        context = (
            f"This user tracks BOTH steps and habits. {lead}\n"
            "Use both step and habit data — lead with whichever is more impressive. "
            "Reference step_rank when it's strong, habit_streak_effective for habit streak."
        )
    elif has_steps:
        context = (
            "This user tracks STEPS ONLY — they have no habit challenge. "
            "Do NOT mention habits, habit streaks, or habit counts anywhere. "
            "Focus entirely on steps, step_streak, steps_daily_target, and step_rank."
        )
    else:
        context = (
            "This user tracks HABITS ONLY — they have no step challenge. "
            "Do NOT mention steps, step_streak, daily step targets, or step_rank anywhere. "
            "Focus entirely on habit_streak_effective, habit completions, and perfect days."
        )

    glossary = (
        "Data field glossary (use these exact names from the JSON below):\n"
        "  step_rank                — leaderboard position in the step challenge (1 = best; null = not in one)\n"
        "  step_streak              — consecutive days user hit their daily step target\n"
        "  habit_streak_effective   — MAIN habit streak: shield-protected consecutive good habit days (use this)\n"
        "  habit_streak_current     — raw habit streak (no shield help) — secondary reference\n"
        "  habit_streak_longest     — best ever shield-protected habit streak\n"
        "  habit_pct_week           — % of all habits completed across this week\n"
        "  habit_perfect_days_week  — days this week where ALL habits were done\n"
        "  steps_*                  — all step-related fields\n"
        "NEVER confuse step_rank (steps leaderboard) with habit streaks.\n"
    )

    return (
        f"{context}\n\n"
        f"{glossary}\n"
        "Here are the user's stats for the past 7 days:\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        "Generate the insight JSON now."
    )


def _validate_spans(spans: list) -> list:
    """Ensure every span has the required keys, strip unknown ones, and fix spacing."""
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

    # Ensure a space exists at every boundary between adjacent spans so the
    # frontend can concatenate them without words running together.
    for i in range(1, len(result)):
        prev, curr = result[i - 1], result[i]
        if curr["text"] and not prev["text"].endswith(" ") and not curr["text"].startswith(" "):
            # Prefer adding the space to the leading edge of the current span
            # so styled spans (stat/highlight/milestone) stay visually clean.
            if curr["style"] == "normal":
                curr["text"] = " " + curr["text"]
            else:
                prev["text"] = prev["text"] + " "

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
    try:
        client = _get_anthropic()
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_user_message(stats)}],
        ) as stream:
            message = await stream.get_final_message()

        raw = next((b.text for b in message.content if b.type == "text"), "{}")
        return _parse_response(raw, stats)

    except Exception as e:
        logger.error(f"Claude insight error: {e}", exc_info=True)
        return _fallback(stats)


async def _ask_azure(stats: dict) -> dict:
    try:
        client = _get_azure()
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM + "\nRespond in valid JSON."},
                {"role": "user",   "content": _build_user_message(stats)},
            ],
            max_completion_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        return _parse_response(raw, stats)

    except Exception as e:
        logger.error(f"Azure insight error: {e}", exc_info=True)
        return _fallback(stats)


# ── fallback ──────────────────────────────────────────────────────────────────

def _fallback(stats: dict) -> dict:
    steps  = stats.get("steps_yesterday", 0)
    done   = stats.get("habits_done_yesterday", 0)
    total  = stats.get("habits_total", 0)
    streak = stats.get("step_streak", 0) or stats.get("habit_streak_effective", 0)

    segments: list = [{"text": f"{steps:,} steps", "style": "stat", "color": "purple"}]
    if total > 0:
        segments += [
            {"text": " and ",                       "style": "normal",    "color": None},
            {"text": f"{done}/{total} habits",       "style": "highlight", "color": "green"},
            {"text": " done yesterday.",             "style": "normal",    "color": None},
        ]
    else:
        segments.append({"text": " logged yesterday.", "style": "normal", "color": None})

    detail: list
    if streak > 0:
        detail = [
            {"text": "Step streak at ",   "style": "normal", "color": None},
            {"text": f"{streak} days",    "style": "stat",   "color": "orange"},
            {"text": " — don't break it.", "style": "normal", "color": None},
        ]
    else:
        detail = [{"text": "Start a streak today — one step at a time.", "style": "normal", "color": None}]

    return {
        "badge":    "Yesterday at a glance",
        "segments": segments,
        "detail":   detail,
        "hook":     "Come back tomorrow to see your progress grow.",
    }
