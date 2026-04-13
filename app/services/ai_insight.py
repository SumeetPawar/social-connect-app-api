"""
AI Insight service — generates a daily personalised summary for the home screen.

Output format (rich text):
    {
      "badge":    str,          # short upbeat label (plain text)
      "segments": [...],        # headline as rich-text spans
      "detail":   [...],        # detail line as rich-text spans
      "hook":     str,          # today's call-to-action / urgency line (plain text)
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

    # ── Habit pack ranking (separate leaderboard from steps) ────────────────
    habit_rank              = None
    habit_rank_change       = None
    habit_total_participants = None
    if habit_challenge_id:
        try:
            hrank_row = await db.execute(text("""
                WITH my_challenge AS (
                    SELECT hc.id, hc.pack_id, COUNT(DISTINCT hcm.id) AS total_habits
                    FROM habit_challenges hc
                    JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
                    WHERE hc.id = :cid
                    GROUP BY hc.id
                ),
                pack_challenges AS (
                    SELECT hc.user_id, hc.id AS challenge_id,
                           COUNT(DISTINCT hcm.id) AS total_habits
                    FROM habit_challenges hc
                    JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
                    JOIN my_challenge mc ON hc.pack_id = mc.pack_id
                    WHERE hc.status = 'active'
                    GROUP BY hc.user_id, hc.id
                ),
                daily_counts AS (
                    SELECT hcm.challenge_id, dl.logged_date,
                           COUNT(*) FILTER (WHERE dl.completed) AS done
                    FROM daily_logs dl
                    JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                    GROUP BY hcm.challenge_id, dl.logged_date
                ),
                user_scores AS (
                    SELECT pc.user_id,
                           COUNT(DISTINCT CASE
                               WHEN dc.done >= GREATEST(1, CEIL(pc.total_habits::numeric / 2))
                               THEN dc.logged_date END) AS good_days
                    FROM pack_challenges pc
                    LEFT JOIN daily_counts dc ON dc.challenge_id = pc.challenge_id
                    GROUP BY pc.user_id
                ),
                daily_counts_yest AS (
                    SELECT hcm.challenge_id, dl.logged_date,
                           COUNT(*) FILTER (WHERE dl.completed) AS done
                    FROM daily_logs dl
                    JOIN habit_commitments hcm ON hcm.id = dl.commitment_id
                    WHERE dl.logged_date < :today
                    GROUP BY hcm.challenge_id, dl.logged_date
                ),
                user_scores_yest AS (
                    SELECT pc.user_id,
                           COUNT(DISTINCT CASE
                               WHEN dc.done >= GREATEST(1, CEIL(pc.total_habits::numeric / 2))
                               THEN dc.logged_date END) AS good_days
                    FROM pack_challenges pc
                    LEFT JOIN daily_counts_yest dc ON dc.challenge_id = pc.challenge_id
                    GROUP BY pc.user_id
                ),
                ranked      AS (SELECT user_id, ROW_NUMBER() OVER (ORDER BY good_days DESC, user_id ASC) AS rnk FROM user_scores),
                ranked_yest AS (SELECT user_id, ROW_NUMBER() OVER (ORDER BY good_days DESC, user_id ASC) AS rnk FROM user_scores_yest)
                SELECT
                    (SELECT rnk FROM ranked      WHERE user_id = :uid) AS habit_rank,
                    (SELECT rnk FROM ranked_yest WHERE user_id = :uid) AS habit_rank_yesterday,
                    (SELECT COUNT(*) FROM user_scores)                  AS total_participants,
                    (SELECT pack_id FROM my_challenge)                  AS pack_id
            """), {"cid": habit_challenge_id, "uid": str(user_id), "today": today})
            hr = hrank_row.mappings().first()
            if hr and hr["pack_id"] and hr["habit_rank"]:
                habit_rank               = int(hr["habit_rank"])
                habit_total_participants = int(hr["total_participants"] or 0)
                if hr["habit_rank_yesterday"]:
                    habit_rank_change = int(hr["habit_rank_yesterday"]) - habit_rank
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
        # Habit challenge has its own separate pack leaderboard (independent of steps)
        "habit_rank":               habit_rank,
        "habit_rank_change":        habit_rank_change,
        "habit_total_participants": habit_total_participants,
    }


# ── shared prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are the motivational coach and mission guide inside a fitness app — sharp, warm, honest, and addictive.
Your job: turn raw 7-day stats (collected through yesterday) into a daily insight the user reads TODAY.
The insight should make them feel personally seen and give them a clear, urgent reason to act RIGHT NOW — today.

TIMING CONTEXT: Stats are from yesterday and earlier. The user is reading this TODAY, mid-day or morning.
  → "detail" = today's single most important mission (what they need to do TODAY to protect/extend their streak or rank)
  → "hook"   = the immediate stakes today — what is ON THE LINE right now, not a future tease

TONE: Coach calling out what matters MOST today, not a recap of yesterday. Every line should feel personally urgent.
ADDICTIVE FEEL: Use mission framing ("today's mission", "today's the day", "this is the moment") and danger-of-loss
  anchors ("one short walk from safety", "don't break the chain today") — urgent without being harsh or judgmental.
HONESTY: Call out what matters most right now — if a streak is at risk TODAY, make it the center. If they're crushing it, celebrate loudly.
Never generic affirmations. Be specific to their actual data.

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
  One scannable line — metric + value + 2–3 word motivational anchor. Max 12 words total.
  This is the user's personal mission for today/this week: urgent, achievable, streak-protective.

  CRITICAL: This app has TWO completely separate challenges with separate leaderboards:
    A) STEPS CHALLENGE — global leaderboard, ranked by cumulative steps.
       Relevant fields: step_streak, step_rank, steps_daily_target, steps_vs_target_pct
       step_rank = position among ALL step challenge users (#1 = most steps)
    B) HABITS CHALLENGE — pack leaderboard (private group), ranked by consistency good-days.
       Relevant fields: habit_streak_effective, habit_rank, habit_pct_week, habit_perfect_days_week
       habit_rank = position among pack members (#1 = most consistent)
    Never mix them. A rank improvement in habits has NOTHING to do with steps, and vice versa.

  PICK THE STRONGEST SIGNAL — scan both challenges and choose the one anchor with the most urgency TODAY:
    PRIORITY ORDER:
    1. STREAK AT RISK — either step or habit streak in 3-6 day danger window (fragile, needs protecting today)
       → "Habit chain: 5 days — one missed day today breaks it"
       → "Step streak: 4 days — one short walk from losing it"
    2. MILESTONE TODAY — either streak is 1 day away from 7/14/21/30 (they can hit it TODAY)
       → "Habit streak: 6 days — today is your first-week badge"
       → "Step streak: 13 days — today could make it 2 weeks straight"
    3. RANK ON THE BRINK — either challenge rank is 1-2 spots from top 3 or #1 (today's effort matters)
       → "Habits: rank #4 in your pack — top 3 is one good day away"
       → "Steps: rank #2 — one strong day from the top"
    4. HABIT STREAK FIRE — strong ongoing streak (7+ days), use today's mission language
       → "Habit chain: 9 days — today's mission: keep the fire alive"
    5. VS WEEKLY TARGET — steps or habit completion % vs goal (close enough that today moves the needle)
       → "Steps: 78% of target this week — today can push you over"
       → "Habits: 5/7 days perfect — today could seal a great week"
    6. PERSONAL RECORD near — effective streak approaching their longest ever
       → "Habit streak: 18 days — 3 from your all-time best"

  TONE RULES (motivational urgency — NOT harsh):
    - Danger framing = protective, not punishing ("one short walk from losing it", "don't break the chain today")
    - Mission language rooted in TODAY: "today's mission", "today is the day", "this is the moment"
    - Never shame or give generic advice ("keep going", "try harder", "you should", "make sure")
    - Numbers always feel personally significant — milestone proximity, personal bests, vs-rank
    - If both challenges are active, pick whichever ONE has higher urgency TODAY — don't cram both into detail

  STYLING:
    - "highlight" for challenge/habit/metric names, "stat" for all numbers, "normal" for anchor text
    - Spaces between spans handled by renderer — never add extra spaces inside text values

"hook"
  One short sentence (max 12 words) that drives TODAY's action — the immediate stakes right now.
  This is the gut-punch that makes them put the phone down and go DO the thing. Make it feel urgent and personal.
  Data is from yesterday — hook is about what TODAY's effort will protect or unlock.

  BOTH CHALLENGES CAN FEED THE HOOK — use whichever creates the highest urgency TODAY:
    STEPS CHALLENGE (global, ranked by total steps):
      • Streak protection: "Hit your target today — that step streak stays alive."
      • Milestone today: "Today's steps could make it 7 days straight."
      • Rank today: "One strong session today keeps the #2 spot."
      • Rank climber: "One solid day today and you're in the top 3."
    HABITS CHALLENGE (pack-based, ranked by good-habit days):
      • Streak protection: "Log your habits today — the chain is still intact."
      • Milestone today: "One good habit day today = your first full week."
      • Rank today: "A consistent day today could move you past #3 in your pack."
      • Perfect week: "All habits today and this week ends as your best yet."
    CROSS-CHALLENGE (when user has both & both have something at stake):
      • "Step streak and habit chain both on the line — protect both today."
      • "Pack rank #2 and step streak at 6 — today is the biggest day this week."

  TONE:
    - Always name a SPECIFIC stake (rank #, streak length, milestone, badge)
    - Personal pronouns ("you", "your") make it feel directed at them, not a template
    - Urgency anchored in today: "today", "right now", "this session", "today is the day"
    - Never generic ("Keep it up!", "You got this!", "Stay consistent!", "Come back tomorrow!")
    - If streak is at risk → protective urgency tone; if milestone within reach → excitement tone
"""


def _build_user_message(stats: dict) -> str:
    has_steps  = stats.get("steps_week", 0) > 0 or stats.get("steps_yesterday", 0) > 0
    has_habits = stats.get("habits_total", 0) > 0
    step_rank   = stats.get("step_rank")
    step_streak = stats.get("step_streak", 0)
    habit_streak = stats.get("habit_streak_effective", 0)
    habit_rank   = stats.get("habit_rank")
    habit_pct    = stats.get("habit_pct_week", 0)
    habit_total_participants = stats.get("habit_total_participants")

    # Build per-challenge context blocks
    steps_context = ""
    habits_context = ""

    if has_steps:
        step_rank_str = f"#{step_rank}" if step_rank else "unranked"
        steps_context = (
            f"STEPS CHALLENGE (global leaderboard — ranked by total steps across all users):\n"
            f"  step_rank = {step_rank_str}  |  step_streak = {step_streak} days  "
            f"|  steps_vs_target_pct = {stats.get('steps_vs_target_pct', 0)}%\n"
        )
        if step_rank and step_rank <= 3:
            steps_context += f"  → Top-3 on the leaderboard — this is impressive, lead with it.\n"
        elif step_rank and step_rank <= 6:
            steps_context += f"  → Just outside top 3 — one strong day could move them up.\n"
        if step_streak >= 5:
            steps_context += f"  → {step_streak}-day step streak — fragile, worth protecting.\n"
        elif step_streak >= 3:
            steps_context += f"  → {step_streak}-day step streak — building momentum.\n"

    if has_habits:
        habit_rank_str = f"#{habit_rank} of {habit_total_participants}" if habit_rank and habit_total_participants else (f"#{habit_rank}" if habit_rank else "unranked")
        habits_context = (
            f"HABITS CHALLENGE (pack leaderboard — ranked by consistency/good-habit days within their group):\n"
            f"  habit_rank = {habit_rank_str}  |  habit_streak_effective = {habit_streak} days  "
            f"|  habit_pct_week = {habit_pct}%\n"
        )
        if habit_rank and habit_rank <= 3:
            habits_context += f"  → Top-3 in pack — strong consistency ranking, celebrate this.\n"
        elif habit_rank and habit_rank <= 6:
            habits_context += f"  → {habit_rank_str} in pack — close to top 3, one good day could move them up.\n"
        if habit_streak >= 14:
            habits_context += f"  → {habit_streak}-day habit streak — significant milestone territory.\n"
        elif 5 <= habit_streak <= 6:
            habits_context += f"  → {habit_streak}-day habit streak — in the danger/milestone window, high urgency.\n"
        elif 3 <= habit_streak < 5:
            habits_context += f"  → {habit_streak}-day habit streak — building, danger framing appropriate.\n"

    if has_steps and has_habits:
        # Identify which challenge has higher urgency for detail and hook focus
        urgency_notes = []
        if step_streak in range(5, 7) or habit_streak in range(5, 7):
            urgency_notes.append("At least one streak is in the 5-6 day danger/milestone window — today's effort protects or extends it.")
        if step_rank and step_rank <= 2:
            urgency_notes.append(f"Step rank is #{step_rank} — one strong day today holds or improves it.")
        if habit_rank and habit_rank <= 2:
            urgency_notes.append(f"Habit pack rank is #{habit_rank} — a consistent day today keeps them near the top.")
        urgency_block = ("  URGENCY NOTES (use to drive today-focused detail and hook):\n" +
                         "\n".join(f"  • {n}" for n in urgency_notes) + "\n") if urgency_notes else ""

        context = (
            "This user participates in BOTH challenges. These are COMPLETELY SEPARATE competitions:\n"
            f"{steps_context}"
            f"{habits_context}"
            f"{urgency_block}"
            "Stats are from yesterday. The user reads this TODAY and needs to act TODAY.\n"
            "Lead segments with the most impressive stat overall. "
            "For detail, pick whichever single challenge has the most urgent story for TODAY's action. "
            "For hook, name the specific stake TODAY's effort will protect or unlock — rank, streak, milestone, or perfect day."
        )
    elif has_steps:
        context = (
            "This user is in the STEPS CHALLENGE ONLY — no habit challenge.\n"
            f"{steps_context}"
            "Stats are from yesterday. The user reads this TODAY and needs to act TODAY.\n"
            "Do NOT mention habits, habit streaks, or habit counts anywhere. "
            "Focus entirely on what TODAY's steps effort will protect or unlock: step_streak, step_rank, steps_daily_target."
        )
    else:
        context = (
            "This user is in the HABITS CHALLENGE ONLY — no step challenge.\n"
            f"{habits_context}"
            "Stats are from yesterday. The user reads this TODAY and needs to act TODAY.\n"
            "Do NOT mention steps, step_streak, daily step targets, or step_rank anywhere. "
            "Focus on what TODAY's habit effort will protect or unlock: habit_streak_effective, habit_rank, perfect days."
        )

    glossary = (
        "Data field glossary (use these exact names from the JSON below):\n"
        "  ── STEPS CHALLENGE (global leaderboard) ──\n"
        "  step_rank                — position on the global step leaderboard (1 = most steps; null = not enrolled)\n"
        "  step_streak              — consecutive days the user hit their daily step target\n"
        "  steps_daily_target       — their personal daily step goal\n"
        "  steps_vs_target_pct      — yesterday's steps as % of their daily target\n"
        "  ── HABITS CHALLENGE (pack/group leaderboard) ──\n"
        "  habit_rank               — position within their habit pack (1 = most consistent; null = no pack)\n"
        "  habit_rank_change        — positive = moved UP ranks, negative = dropped (null if unknown)\n"
        "  habit_total_participants — total members in their habit pack\n"
        "  habit_streak_effective   — MAIN habit streak: shield-protected consecutive good habit days (use this)\n"
        "  habit_streak_current     — raw habit streak (no shield protection) — secondary reference only\n"
        "  habit_streak_longest     — personal best shield-protected habit streak (ever)\n"
        "  habit_pct_week           — % of all habits completed this week\n"
        "  habit_perfect_days_week  — days this week where ALL habits were completed\n"
        "CRITICAL: step_rank and habit_rank are DIFFERENT leaderboards. Never mix or confuse them.\n"
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
        "hook":     "One step today keeps the streak alive — make it count.",
    }
