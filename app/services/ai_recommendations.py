"""
AI Recommendations service — three independent on-demand AI features:

  1. body_insight  — GET /api/body-metrics/insight
     Analyses body composition trend across all scans.
     Output: { trend_summary, highlights: [{metric, direction, note}], warning, tip }

  2. habit_picks   — GET /api/habits/recommend
     Picks the 3 best habits to try next cycle based on profile + history.
     Output: { picks: [{slug, label, why, category, tier}], intro }

  3. step_goal     — GET /api/goals/suggest
     Suggests whether to raise / lower / keep the daily step target.
     Output: { action: "raise"|"lower"|"keep", suggested_target, reason, confidence }

All three:
  - Cache result in ai_recommendations table for 7 days
  - Fall back to rule-based output if AI call fails
  - Use AI_PROVIDER env var (azure / anthropic)
"""
import json
import logging
from datetime import datetime, date, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AiRecommendation

logger = logging.getLogger(__name__)

_CACHE_DAYS = 7
_VALID_TARGETS = [3000, 5000, 7500, 8000, 9000, 10000]


# ── cache helpers ─────────────────────────────────────────────────────────────

async def _get_cached(db: AsyncSession, user_id: str, rec_type: str) -> dict | None:
    row = await db.execute(
        select(AiRecommendation)
        .where(
            AiRecommendation.user_id == user_id,
            AiRecommendation.type == rec_type,
        )
        .order_by(AiRecommendation.created_at.desc())
        .limit(1)
    )
    rec = row.scalar_one_or_none()
    if rec and (datetime.now(timezone.utc) - rec.created_at).days < _CACHE_DAYS:
        return {**rec.payload, "generated_at": rec.created_at.isoformat(), "cached": True}
    return None


async def _save(db: AsyncSession, user_id: str, rec_type: str,
                payload: dict, stats: dict) -> None:
    try:
        db.add(AiRecommendation(
            user_id=user_id,
            type=rec_type,
            provider=settings.AI_PROVIDER.lower(),
            payload=payload,
            raw_stats=stats,
        ))
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"AiRecommendation save error ({rec_type}, user {user_id}): {e}")


# ── shared AI call ────────────────────────────────────────────────────────────

async def _ask_ai(system: str, user_msg: str) -> str:
    """Returns raw JSON string from whichever provider is configured."""
    provider = settings.AI_PROVIDER.lower()

    if provider == "azure":
        from openai import AsyncAzureOpenAI
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system + "\nRespond in valid JSON."},
                {"role": "user",   "content": user_msg},
            ],
            max_completion_tokens=1200,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"

    else:  # anthropic
        import anthropic
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=1200,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            message = await stream.get_final_message()
        return next((b.text for b in message.content if b.type == "text"), "{}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. BODY METRICS INSIGHT
# ══════════════════════════════════════════════════════════════════════════════

_BODY_SYSTEM = """\
You are a personal health coach reviewing a user's body composition data.
Your job: turn raw scan numbers into insights the user can actually act on today.

Rules:
- No jargon. A 16-year-old should understand every word.
- Be specific — reference exact numbers, not vague trends.
- Every insight must have a "so what" — why does this number matter to the user's daily life?
- The best action must be concrete: "walk 20 minutes after dinner", not "exercise more".
- Celebrate genuine wins. Flag real concerns calmly, never alarm.

Return ONLY valid JSON (no markdown, no code fences) with exactly these keys:

─── SPAN FORMAT (used in every rich-text field) ───
Array of span objects: { "text": "...", "style": "...", "color": "..." }

  style:
    "normal"    → plain text
    "stat"      → a number or metric value — render bold with accent color
    "highlight" → a metric name as a label — render as colored chip/pill
    "bold"      → emphasis phrase — render bold, no background

  color (use only these, or null):
    "green"   → improvement, good news
    "rose"    → concern, needs attention
    "orange"  → caution, mild risk
    "purple"  → steps / activity metrics
    "teal"    → action, tip, recommendation

  SPACING RULE: spans join with no gap. Always put a space at the start or end
  of "normal" spans that sit between two styled spans.

─── KEYS ───

"summary"
  Rich text. 1-2 sentences, ≤25 words.
  Open with the single most meaningful change or achievement.
  If only 1 scan: summarise current state and what to watch.
  Must include at least 1 "stat" span and 1 "highlight" span.

"highlights"
  Array of 2-4 metric cards, ordered by PRIORITY — most urgent or impactful first.
  Priority order: (1) active concern/risk, (2) biggest positive change, (3) stable metrics.
  Each card:
  {
    "metric":    str          — short human label: "Body fat", "Muscle mass", "Visceral fat", "Hydration"
    "direction": "up"|"down"|"stable"
    "value":     str          — current value with unit: "18.2%", "32.1 kg", "Level 8"
    "delta":     str|null     — signed change from first scan: "-1.4%", "+0.8 kg", null if 1 scan
    "priority":  "high"|"medium"|"low"   — high = needs attention or biggest win, low = informational
    "note":      rich text    — ≤12 words. Answer: "what does this mean for me right now?"
      Good examples:
        Body fat down → [{"text":"In the healthy range","style":"bold","color":"green"},{"text":" — your diet is working.","style":"normal","color":null}]
        Visceral fat high → [{"text":"High visceral fat","style":"highlight","color":"rose"},{"text":" stresses your organs. Reduce refined sugar.","style":"normal","color":null}]
        Muscle stable → [{"text":"Holding strong","style":"bold","color":"teal"},{"text":" — add resistance training to grow it.","style":"normal","color":null}]
  }
  Pick metrics with actual data. Skip metrics with null values.
  Color per direction:
    fat/visceral going down → green. Going up → rose.
    muscle/hydration going up → green. Going down → rose.
    stable → teal.

"warning"
  null if nothing concerning.
  Otherwise rich text, ≤15 words.
  Only trigger for: visceral fat > 12, metabolic age > biological age + 5, body fat in obese range.
  Use "orange" for caution, "rose" only if genuinely important.

"action"
  The single most impactful thing the user can do RIGHT NOW based on their data.
  Rich text, ≤20 words. Must be specific — include a frequency, duration, or quantity.
  Must reference the metric it targets as a "highlight" span.
  Start with a verb. Use "teal" for the action itself.
  Examples:
    [{"text":"Walk ","style":"stat","color":"teal"},{"text":"20 min after dinner","style":"bold","color":"teal"},{"text":" — the fastest way to reduce ","style":"normal","color":null},{"text":"visceral fat","style":"highlight","color":"orange"},{"text":".","style":"normal","color":null}]
    [{"text":"Add ","style":"normal","color":null},{"text":"25g protein","style":"stat","color":"green"},{"text":" per meal to protect your ","style":"normal","color":null},{"text":"muscle mass","style":"highlight","color":"green"},{"text":" while losing fat.","style":"normal","color":null}]
"""


async def _collect_body_stats(db: AsyncSession, user_id: str) -> dict | None:
    from app.models import BodyMetrics
    rows = await db.execute(
        select(BodyMetrics)
        .where(BodyMetrics.user_id == user_id)
        .order_by(BodyMetrics.recorded_date.asc())
    )
    scans = rows.scalars().all()
    if len(scans) < 1:
        return None

    def _f(v):
        return float(v) if v is not None else None

    def _scan(s):
        return {
            "date":                str(s.recorded_date),
            "weight_kg":           _f(s.weight_kg),
            "bmi":                 _f(s.bmi),
            "body_fat_pct":        _f(s.body_fat_pct),
            "skeletal_muscle_pct": _f(s.skeletal_muscle_pct),
            "visceral_fat":        _f(s.visceral_fat),
            "metabolic_age":       s.metabolic_age,
            "bmr_kcal":            s.bmr_kcal,
            "hydration_pct":       _f(s.hydration_pct),
            "protein_pct":         _f(s.protein_pct),
        }

    first = _scan(scans[0])
    latest = _scan(scans[-1])
    total_scans = len(scans)

    # Simple deltas (latest − first)
    def _delta(key):
        a, b = first.get(key), latest.get(key)
        if a is None or b is None:
            return None
        return round(b - a, 2)

    return {
        "total_scans": total_scans,
        "first_scan":  first,
        "latest_scan": latest,
        "deltas": {
            "weight_kg":           _delta("weight_kg"),
            "body_fat_pct":        _delta("body_fat_pct"),
            "skeletal_muscle_pct": _delta("skeletal_muscle_pct"),
            "visceral_fat":        _delta("visceral_fat"),
            "metabolic_age":       _delta("metabolic_age"),
            "hydration_pct":       _delta("hydration_pct"),
        },
        "days_tracked": (scans[-1].recorded_date - scans[0].recorded_date).days,
    }


def _span(text: str, style: str = "normal", color: str | None = None) -> dict:
    return {"text": text, "style": style, "color": color}


def _validate_spans(spans: object) -> list:
    if not isinstance(spans, list):
        return [_span(str(spans))]
    valid_styles = {"normal", "stat", "highlight", "bold"}
    valid_colors = {"green", "rose", "orange", "purple", "teal", None}
    out = []
    for s in spans:
        if not isinstance(s, dict) or "text" not in s:
            continue
        out.append({
            "text":  str(s.get("text", "")),
            "style": s.get("style") if s.get("style") in valid_styles else "normal",
            "color": s.get("color") if s.get("color") in valid_colors else None,
        })
    return out or [_span("—")]


def _fallback_body(stats: dict) -> dict:
    latest = stats.get("latest_scan", {})
    bf = latest.get("body_fat_pct")
    sm = latest.get("skeletal_muscle_pct")
    n  = stats.get("total_scans", 1)
    return {
        "summary": [
            _span(f"{n} scan{'s' if n > 1 else ''} logged — ", "normal"),
            _span("here's your body composition snapshot", "bold"),
            _span(".", "normal"),
        ],
        "highlights": [
            {
                "metric":    "Body fat",
                "direction": "stable",
                "value":     f"{bf}%" if bf else "—",
                "delta":     None,
                "priority":  "medium",
                "note":      [_span("Current baseline — log more scans to see trends.", "normal")],
            },
            {
                "metric":    "Skeletal muscle",
                "direction": "stable",
                "value":     f"{sm}%" if sm else "—",
                "delta":     None,
                "priority":  "low",
                "note":      [_span("Strength foundation — keep training consistently.", "normal")],
            },
        ],
        "warning": None,
        "action": [
            _span("Log scans ", "normal"),
            _span("monthly", "highlight", "teal"),
            _span(" to unlock personalised trend insights.", "normal"),
        ],
    }


async def get_body_insight(db: AsyncSession, user_id: str) -> dict | None:
    cached = await _get_cached(db, user_id, "body_insight")
    if cached:
        return cached

    stats = await _collect_body_stats(db, user_id)
    if stats is None:
        return None

    try:
        raw = await _ask_ai(
            _BODY_SYSTEM,
            f"Body composition data:\n{json.dumps(stats, indent=2)}\n\nGenerate the analysis JSON."
        )
        data = json.loads(raw)

        _priority_order = {"high": 0, "medium": 1, "low": 2}
        raw_highlights = [h for h in data.get("highlights", []) if isinstance(h, dict)]
        raw_highlights.sort(key=lambda h: _priority_order.get(h.get("priority", "low"), 2))
        highlights = []
        for h in raw_highlights[:4]:
            highlights.append({
                "metric":    str(h.get("metric", "")),
                "direction": str(h.get("direction", "stable")),
                "value":     str(h.get("value", "—")),
                "delta":     str(h.get("delta")) if h.get("delta") else None,
                "priority":  str(h.get("priority", "low")),
                "note":      _validate_spans(h.get("note", [])),
            })

        warning_raw = data.get("warning")
        payload = {
            "summary":    _validate_spans(data.get("summary", [])),
            "highlights": highlights,
            "warning":    _validate_spans(warning_raw) if warning_raw else None,
            "action":     _validate_spans(data.get("action", [])),
        }
    except Exception as e:
        logger.error(f"Body insight AI error for user {user_id}: {e}", exc_info=True)
        payload = _fallback_body(stats)

    await _save(db, user_id, "body_insight", payload, stats)
    return {**payload, "generated_at": datetime.now(timezone.utc).isoformat(), "cached": False}


# ══════════════════════════════════════════════════════════════════════════════
# 2. HABIT RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════════

_HABIT_SYSTEM = """\
You are a habit coach helping a user choose their next 3 habits for a 7-day challenge.
You will be given: the user's profile, their habit history (what they've tried and how well they did),
and the full list of available habits with descriptions.

Your job: pick exactly 3 habits that will have the highest impact for this specific user.

Rules:
- Do NOT recommend habits they currently have active
- Prioritise habits they haven't tried yet over ones they failed repeatedly
- Balance across categories (Body, Mind, Lifestyle) where possible
- Consider their activity level, age, gender
- Give an honest, specific reason why each habit suits them

Return ONLY valid JSON (no markdown) with these keys:

"intro"
  1-2 sentences addressing the user directly. Reference something specific
  from their history (e.g. "You've been consistent with your morning walk —
  now it's time to stack a mindfulness habit on top.")

"picks"
  Array of exactly 3 objects:
  {
    "slug":     str,     — must match a slug from the available habits list
    "label":    str,     — the habit's display name
    "category": str,     — "Body" | "Mind" | "Lifestyle"
    "tier":     str,     — "core" | "growth" | "avoid"
    "why":      str      — 1-2 sentences specific to this user's data
  }
"""


async def _collect_habit_stats(db: AsyncSession, user_id: str, user: object) -> dict:
    from app.models import Habit

    # All habits in the library
    all_habits_rows = await db.execute(
        select(Habit).order_by(Habit.category, Habit.label)
    )
    all_habits = [
        {"slug": h.slug, "label": h.label, "description": h.description,
         "why": h.why, "category": h.category.value, "tier": h.tier.value}
        for h in all_habits_rows.scalars().all()
    ]

    # Habits currently active
    active_rows = await db.execute(text("""
        SELECT hb.slug
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        WHERE hc.user_id = :uid AND hc.status = 'active'
    """), {"uid": user_id})
    active_slugs = {r[0] for r in active_rows.all()}

    # Past challenge history with completion %
    history_rows = await db.execute(text("""
        SELECT
            hb.slug,
            hb.label,
            hb.category,
            hc.started_at,
            hc.ends_at,
            hc.status,
            COUNT(dl.id) FILTER (WHERE dl.completed) AS done,
            COUNT(hcm.id) * GREATEST((hc.ends_at - hc.started_at + 1), 7) AS possible
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status IN ('completed', 'abandoned')
        GROUP BY hb.slug, hb.label, hb.category, hc.started_at, hc.ends_at, hc.status
        ORDER BY hc.started_at DESC
    """), {"uid": user_id})
    history = [
        {
            "slug":      r["slug"],
            "label":     r["label"],
            "category":  r["category"],
            "completed": r["status"] == "completed",
            "pct":       round(int(r["done"]) / max(int(r["possible"]), 1) * 100),
        }
        for r in history_rows.mappings().all()
    ]

    return {
        "user_profile": {
            "age":            user.age,
            "gender":         user.gender,
            "activity_level": user.activity_level,
        },
        "active_habit_slugs": list(active_slugs),
        "past_cycles":        history,
        "available_habits":   [h for h in all_habits if h["slug"] not in active_slugs],
    }


def _fallback_habits(stats: dict) -> dict:
    available = stats.get("available_habits", [])
    # Just pick one from each category
    picks = []
    seen_cats = set()
    for h in available:
        if h["category"] not in seen_cats and h["tier"] == "core":
            picks.append({
                "slug":     h["slug"],
                "label":    h["label"],
                "category": h["category"],
                "tier":     h["tier"],
                "why":      h["description"],
            })
            seen_cats.add(h["category"])
            if len(picks) == 3:
                break
    return {
        "intro": "Here are three habits to try for your next cycle.",
        "picks": picks,
    }


async def get_habit_recommendations(db: AsyncSession, user_id: str, user: object) -> dict:
    cached = await _get_cached(db, user_id, "habit_picks")
    if cached:
        return cached

    stats = await _collect_habit_stats(db, user_id, user)

    try:
        raw = await _ask_ai(
            _HABIT_SYSTEM,
            f"User data:\n{json.dumps(stats, indent=2)}\n\nPick the best 3 habits."
        )
        data = json.loads(raw)
        picks = []
        for p in data.get("picks", [])[:3]:
            if not isinstance(p, dict) or "slug" not in p:
                continue
            picks.append({
                "slug":     str(p.get("slug", "")),
                "label":    str(p.get("label", "")),
                "category": str(p.get("category", "")),
                "tier":     str(p.get("tier", "")),
                "why":      str(p.get("why", "")),
            })
        payload = {
            "intro": str(data.get("intro", "")),
            "picks": picks,
        }
    except Exception as e:
        logger.error(f"Habit recommendation AI error for user {user_id}: {e}")
        payload = _fallback_habits(stats)

    await _save(db, user_id, "habit_picks", payload, stats)
    return {**payload, "generated_at": datetime.now(timezone.utc).isoformat(), "cached": False}


# ══════════════════════════════════════════════════════════════════════════════
# 3. STEP GOAL ADVISOR
# ══════════════════════════════════════════════════════════════════════════════

_GOAL_SYSTEM = """\
You are a fitness coach advising a user on their daily step goal.
Be honest. Don't recommend raising the target if the user is struggling.
Don't keep the target low if the user is comfortably beating it every day.

Return ONLY valid JSON (no markdown) with these keys:

"action"
  One of: "raise" | "lower" | "keep"
  "raise" — user is consistently exceeding target, ready for a bigger challenge
  "lower" — user is consistently missing target, a lower target builds momentum
  "keep"  — target is appropriately challenging right now

"suggested_target"
  Integer. Must be one of: 3000, 5000, 7500, 8000, 9000, 10000.
  If action is "keep", this equals the current target.
  If action is "raise", pick the next level up.
  If action is "lower", pick the next level down.

"reason"
  2-3 sentences. Explain WHY you're making this recommendation using specific
  numbers from the data. Reference hit rate, average steps, trend.
  Speak directly to the user as "you". Plain language.

"confidence"
  "high"   — clear signal from 14+ days of data
  "medium" — reasonable signal but limited data
  "low"    — not enough data to be confident, this is a rough estimate
"""


async def _collect_goal_stats(db: AsyncSession, user_id: str) -> dict:
    today = date.today()
    start_30 = today - timedelta(days=29)
    start_14 = today - timedelta(days=13)
    start_7  = today - timedelta(days=6)

    steps_row = await db.execute(text("""
        SELECT
            COALESCE(ROUND(AVG(steps)), 0)                  AS avg_30,
            COALESCE(ROUND(AVG(CASE WHEN day >= :s14 THEN steps END)), 0) AS avg_14,
            COALESCE(ROUND(AVG(CASE WHEN day >= :s7  THEN steps END)), 0) AS avg_7,
            COUNT(*)                                         AS days_logged,
            MAX(steps)                                       AS best_day,
            COALESCE(SUM(CASE WHEN steps > 0 THEN 1 ELSE 0 END), 0) AS active_days
        FROM daily_steps
        WHERE user_id = :uid AND day >= :s30 AND day <= :today
    """), {"uid": user_id, "s30": start_30, "s14": start_14, "s7": start_7, "today": today})
    s = steps_row.mappings().first() or {}

    target_row = await db.execute(text("""
        SELECT
            COALESCE(cp.selected_daily_target, 8000) AS daily_target,
            COUNT(ds.day) FILTER (WHERE ds.steps >= COALESCE(cp.selected_daily_target, 8000)) AS days_hit_30,
            COUNT(ds.day) FILTER (WHERE ds.day >= :s14 AND ds.steps >= COALESCE(cp.selected_daily_target, 8000)) AS days_hit_14,
            COUNT(ds.day) FILTER (WHERE ds.day >= :s7  AND ds.steps >= COALESCE(cp.selected_daily_target, 8000)) AS days_hit_7
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        LEFT JOIN daily_steps ds
            ON ds.user_id = cp.user_id AND ds.day >= :s30 AND ds.day <= :today
        WHERE cp.user_id = :uid AND c.status = 'active' AND cp.left_at IS NULL
        ORDER BY c.end_date DESC LIMIT 1
    """), {"uid": user_id, "s30": start_30, "s14": start_14, "s7": start_7, "today": today})
    t = target_row.mappings().first() or {}

    current_target = int(t.get("daily_target") or 8000)
    days_hit_30    = int(t.get("days_hit_30") or 0)
    days_hit_14    = int(t.get("days_hit_14") or 0)
    days_hit_7     = int(t.get("days_hit_7") or 0)
    active_days    = int(s.get("active_days") or 0)

    return {
        "current_target":      current_target,
        "available_targets":   _VALID_TARGETS,
        "avg_steps_30d":       int(s.get("avg_30") or 0),
        "avg_steps_14d":       int(s.get("avg_14") or 0),
        "avg_steps_7d":        int(s.get("avg_7") or 0),
        "best_day_steps":      int(s.get("best_day") or 0),
        "active_days_30d":     active_days,
        "target_hit_pct_30d":  round(days_hit_30 / max(active_days, 1) * 100) if active_days else 0,
        "target_hit_pct_14d":  round(days_hit_14 / 14 * 100),
        "target_hit_pct_7d":   round(days_hit_7  / 7  * 100),
    }


def _fallback_goal(stats: dict) -> dict:
    current = stats.get("current_target", 8000)
    hit_pct = stats.get("target_hit_pct_30d", 0)
    avg = stats.get("avg_steps_30d", 0)

    if hit_pct >= 80 and avg > current:
        idx = _VALID_TARGETS.index(current) if current in _VALID_TARGETS else 3
        suggested = _VALID_TARGETS[min(idx + 1, len(_VALID_TARGETS) - 1)]
        action = "raise"
    elif hit_pct < 40:
        idx = _VALID_TARGETS.index(current) if current in _VALID_TARGETS else 3
        suggested = _VALID_TARGETS[max(idx - 1, 0)]
        action = "lower"
    else:
        suggested = current
        action = "keep"

    return {
        "action":           action,
        "suggested_target": suggested,
        "reason":           f"You're hitting your {current:,} step goal {hit_pct}% of the time with an average of {avg:,} steps/day.",
        "confidence":       "medium",
    }


async def get_step_goal_suggestion(db: AsyncSession, user_id: str) -> dict:
    cached = await _get_cached(db, user_id, "step_goal")
    if cached:
        return cached

    stats = await _collect_goal_stats(db, user_id)

    try:
        raw = await _ask_ai(
            _GOAL_SYSTEM,
            f"Step data:\n{json.dumps(stats, indent=2)}\n\nGenerate the goal recommendation JSON."
        )
        data = json.loads(raw)
        suggested = int(data.get("suggested_target", stats["current_target"]))
        # Snap to nearest valid target
        if suggested not in _VALID_TARGETS:
            suggested = min(_VALID_TARGETS, key=lambda x: abs(x - suggested))
        payload = {
            "action":           str(data.get("action", "keep")),
            "suggested_target": suggested,
            "current_target":   stats["current_target"],
            "reason":           str(data.get("reason", "")),
            "confidence":       str(data.get("confidence", "medium")),
        }
    except Exception as e:
        logger.error(f"Step goal AI error for user {user_id}: {e}")
        payload = {**_fallback_goal(stats), "current_target": stats["current_target"]}

    await _save(db, user_id, "step_goal", payload, stats)
    return {**payload, "generated_at": datetime.now(timezone.utc).isoformat(), "cached": False}
