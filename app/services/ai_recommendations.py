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
            max_completion_tokens=3000,
            response_format={"type": "json_object"},
        )
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "length":
            logger.warning("_ask_ai: Azure response truncated (finish_reason=length) — increase max_completion_tokens")
        return response.choices[0].message.content or "{}"

    else:  # anthropic
        import anthropic
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            message = await stream.get_final_message()
        return next((b.text for b in message.content if b.type == "text"), "{}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. BODY METRICS INSIGHT
# ══════════════════════════════════════════════════════════════════════════════

_BODY_SYSTEM = """\
You are the health coach inside a wellness app. You just reviewed this user's body scan.
Your job: tell them exactly what you see, what it means for their daily life, and the ONE thing
that will move their numbers — so clearly they act on it today, not someday.

═══ HOW TO WRITE ═══
Talk like a knowledgeable friend, not a clinic report.
• Short sentences. Everyday words. Zero jargon.
  NEVER write: "cardiovascular", "visceral adiposity", "insulin", "glycaemic", "adipose tissue".
  Simplify explanations but NEVER rename metrics — always use the exact scanner metric name:
    "Visceral fat" (not belly fat), "Body fat %", "Skeletal muscle %", "BMR", "Metabolic age".
  Plain alternatives for explanations only (not metric names):
    metabolic → "how your body burns fuel" | cardiovascular → "your heart and blood vessels"
    blood sugar spike → "your body stores the extra as fat"
• Use their exact numbers every time. "Level 14" not "high". "31%" not "low".
• Connect every metric to real life — energy, how clothes fit, how they feel in the morning.
• Explain what visceral fat IS in plain terms when relevant:
    "Visceral fat sits around your organs — high levels slow your metabolism and drain your energy."
• No fear. No doom. Every sentence ends pointing forward.
• If a habit caused a change → say it directly: "Your walk habit brought visceral fat from Level 14 to 12."

═══ HEALTHY RANGES ═══
"ideal_ranges" in the input is pre-computed for this user's exact age, gender, activity level,
and height. Each key = [low, high]. Outside either end = needs attention.
Special case: high BMR + high body fat = strong muscle under the fat. Call it an asset.

═══ READ THE HABIT DATA ═══
"active_habits" = what they're doing now. pct_window = how consistently, over the last 15 days.
"habit_library" = all available in-app habits (slug, label, impact, category).

For the user's #1 concern, pick exactly ONE response:
  WIN    — habit ≥70% done + metric improved   → celebrate + credit the habit explicitly
  HOLD   — habit ≥70% done + metric unchanged  → encourage: "right habit, give it more time"
  PUSH   — habit active but <70% done          → be honest: "you have the right habit — log it every day"
  UNLOCK — no relevant habit active at all     → prescribe the single best action for this metric

═══ WHAT WORKS (use your full knowledge) ═══
Draw on research. Translate everything into plain language. Examples:

Visceral fat too high:
  Walk 20 min after your biggest meal — steadies blood sugar, visceral fat responds fastest to this
  Eat within a 10–12 hour window — gives the body time overnight to burn stored fat
  Strength training 3×/week — muscle built this way burns visceral fat around the clock
  Cut heavy carbs at dinner — the body stores more fat from carbs eaten late at night

Body fat too high:
  Protein at every meal — keeps hunger down, preserves muscle while losing fat
  Strength training 3×/week — more muscle = more fat burned even doing nothing
  Sleep 7–9 hours — bad sleep makes you hungrier and slows fat burning the next day
  Vegetables first, half the plate — naturally cuts how much you eat without counting calories

Muscle too low:
  Lift weights or bodyweight training 3–4×/week — the only proven way to build muscle
  Protein at every meal — muscles cannot grow without it
  8+ hours sleep — most muscle repair happens overnight

Metabolic age too high:
  Short intense exercise 2×/week (10–20 min) — fastest way to turn back the clock
  Cold shower 2–3 min — forces the body to burn energy to warm up, wakes up fat cells
  7,000–10,000 steps every day — the single biggest lever for metabolic age

Hydration too low:
  Full glass of water before every meal — easiest habit, biggest hydration impact
  Pinch of salt in morning water — helps the body actually hold and use the water

Return ONLY valid JSON — no markdown, no code fences.

─── SPAN FORMAT ───
Rich-text fields = array of { "text": str, "style": str, "color": str|null }
  style: "normal" | "stat" | "highlight" | "bold"
  color: "green" | "rose" | "orange" | "purple" | "teal" | null
  No gaps between spans — put spaces inside the text strings where needed.

─── OUTPUT ───

"headline"
  ≤8 words. One specific, honest finding. Hook the user immediately.
  Something improved → "Visceral fat down 2 levels — walk habit is working"
  Concern, no habit → "Visceral fat at Level 14 — one habit can change this"
  Concern, habit exists → "Right habit — now log it every single day"
  Plain string. No spans.

"story"
  3 sentences, ≤50 words total.
  S1 — The #1 metric: exact value, change since last scan if available.
  S2 — What it means for daily life right now (energy, strength, how clothes fit — never disease).
  S3 — WIN/HOLD/PUSH: credit or push the habit. UNLOCK: "one habit can start changing this in X weeks."
  Rich text spans. Must include ≥1 "stat" span (numbers) and ≥1 "highlight" or "bold" span (habit/metric names).

"highlights"
  2–4 cards. Most urgent first, then wins, then stable.
  {
    "metric":    "Visceral fat" | "Body fat" | "Skeletal muscle" | "BMR" | "Hydration" | "Weight" | "Metabolic age"
    "direction": "up" | "down" | "stable"
    "value":     exact value with unit — "Level 14", "29.4%", "1 865 kcal"
    "delta":     change vs first scan — "-2 levels", "+1.4%", null if only 1 scan
    "priority":  "high" | "medium" | "low"
    "note":      rich text ≤12 words — where they stand + one plain-English implication or habit link
  }
  Omit metrics with null values.
  note color: concern/rising → orange or rose | improving → green | healthy/stable → teal | stable+concern → orange

"focus"
  The ONE action that matters most right now. ≤18 words. Starts with a verb.
  WIN:    "Keep [habit] going every day — it's directly moving your [metric]."
  HOLD:   "Keep [habit] going. The number will shift — it needs 4–6 more weeks."
  PUSH:   "Log [habit] every single day this week. That's the only thing that moves [metric]."
  UNLOCK: Exact action — what, how long, how often. From library or your own knowledge.
  Key action words → color "teal". Metric name → style "highlight".

"next_milestone"
  Plain string ≤15 words. What the NEXT scan will show if they act on focus now.
  Specific. Tied to their numbers. Creates a reason to scan again.
  "Keep the walk habit — next scan could show visceral fat at Level 12."
  "Log protein every day — next scan will show muscle holding or growing."

"suggested_habits"
  1–3 habits prescribed for this person based on their actual scan numbers.
  You are NOT limited to habit_library — recommend whatever is most effective.
  Use library habits when they fit (reference by exact label). Add new ones when they don't.
  Make each feel necessary, not optional. Their numbers give you leverage — use it.
  [
    {
      "name":       short verb-first name — "Walk after dinner", "Sleep by 10pm", "Protein at every meal"
      "in_library": true if the habit's slug exists in habit_library, else false
      "slug":       matching slug from library, or null
      "category":   "nutrition" | "fitness" | "wellness" | "sleep" | "mindset"
      "frequency":  "daily" | "3×/week" | "weekly" | etc.
      "duration":   "20 min" | "10 min" | null if not time-based

      "why": ONE sentence. Their actual number + what this habit does to it + how fast.
        ✓ "Your visceral fat is at Level 14 — a 20-min walk after dinner is the fastest way to bring it down."
        ✓ "Your muscle is at 31%, below the healthy range — lifting 3×/week will shift this in 6 weeks."
        ✗ "This supports overall health." (no number, no impact, no urgency)

      "urgency": ONE sentence. Make them feel the cost of waiting — or the thrill of being close.
        "Every week without this, visceral fat stays at Level 14 — or goes higher."
        "Two consistent weeks and your next scan will look noticeably different."
        "You are one habit away from visceral fat starting to drop."
        Always tied to their actual number. Never generic.

      "first_step": The exact thing they should do TODAY. Concrete. Immediate. No vagueness.
        ✓ "After dinner tonight, put your shoes on and walk for 20 minutes."
        ✓ "At your next meal, add an egg or a handful of nuts before anything else."
        ✓ "Tonight, set a 10pm alarm — that's your sleep start. Stick to it."
        This is the most important field. It turns intent into action.
    }
  ]
"""


def _compute_ideal_ranges(age: int | None, gender: str | None,
                          activity: str | None,
                          height_cm: float | None, weight_kg: float | None) -> dict:
    """
    Mirror of frontend computeRanges — age/gender/activity-adjusted healthy ranges.
    Returns the same structure the UI uses so AI analysis is consistent.
    """
    age      = age or 30
    h        = (height_cm or 170) / 100
    w        = weight_kg or 70
    is_male  = (gender or "").lower() in ("male", "m")

    # BMI / weight
    bmi_ideal = (20.0, 24.9) if age > 60 else (18.5, 22.9)
    act_bonus = {"athlete": 2.5, "active": 1.5, "moderate": 0.5}.get(activity or "", 0.0)
    weight_range = (round(bmi_ideal[0] * h * h, 1),
                    round((bmi_ideal[1] + act_bonus) * h * h, 1))

    # Body fat %
    if is_male:
        fat = (10, 19) if age < 40 else (11, 21) if age < 60 else (13, 24)
    else:
        fat = (20, 28) if age < 40 else (21, 30) if age < 60 else (22, 33)

    # BMR — Mifflin-St Jeor
    bmr_base = (10*w + 6.25*(height_cm or 170) - 5*age + (5 if is_male else -161))
    bmr_range = (round(bmr_base * 0.92), round(bmr_base * 1.08))

    # Metabolic age ideal: [age-10, age] (not older than real age)
    mage_range = (max(18, age - 10), max(19, age))

    # Skeletal muscle %
    if is_male:
        skel = (33, 39) if age < 40 else (31, 37) if age < 60 else (29, 35)
    else:
        skel = (24, 30) if age < 40 else (23, 29) if age < 60 else (21, 27)

    return {
        "weight_kg":           list(weight_range),
        "bmi":                 list(bmi_ideal),
        "body_fat_pct":        list(fat),
        "visceral_fat":        [1, 9],
        "bmr_kcal":            list(bmr_range),
        "metabolic_age":       list(mage_range),
        "skeletal_muscle_pct": list(skel),
    }


async def _collect_body_stats(db: AsyncSession, user_id: str) -> dict | None:
    from app.models import BodyMetrics, User
    rows = await db.execute(
        select(BodyMetrics)
        .where(BodyMetrics.user_id == user_id)
        .order_by(BodyMetrics.recorded_date.asc())
    )
    scans = rows.scalars().all()
    if len(scans) < 1:
        return None

    # Fetch user profile for gender/age/height context
    user_row = await db.execute(select(User).where(User.id == user_id))
    user = user_row.scalar_one_or_none()
    user_gender   = getattr(user, "gender",         None) if user else None
    user_age      = getattr(user, "age",             None) if user else None
    user_activity = getattr(user, "activity_level",  None) if user else None
    user_height   = float(getattr(user, "height_cm", None) or 0) or None

    # Fetch active habits (name + 7-day completion %) to connect behaviour to metrics
    # Use 15-day window to match typical scan cadence (scans every ~15 days)
    habit_window_days = 15
    habit_window_start = date.today() - timedelta(days=habit_window_days - 1)
    habit_rows = await db.execute(text("""
        SELECT
            hb.slug,
            hb.label,
            hb.category,
            COUNT(dl.id) FILTER (WHERE dl.completed AND dl.logged_date >= :window_start) AS done_window
        FROM habit_challenges hc
        JOIN habit_commitments hcm ON hcm.challenge_id = hc.id
        JOIN habits hb ON hb.id = hcm.habit_id
        LEFT JOIN daily_logs dl ON dl.commitment_id = hcm.id
        WHERE hc.user_id = :uid AND hc.status = 'active'
        GROUP BY hb.slug, hb.label, hb.category
    """), {"uid": str(user_id), "window_start": habit_window_start})
    active_habits = [
        {
            "slug":         r["slug"],
            "label":        r["label"],
            "category":     r["category"],
            "done_window":  int(r["done_window"] or 0),
            "pct_window":   round(int(r["done_window"] or 0) / habit_window_days * 100),
        }
        for r in habit_rows.mappings().all()
    ]

    # Fetch average steps (last 7 days) as activity proxy
    steps_row = await db.execute(text("""
        SELECT COALESCE(ROUND(AVG(steps)), 0) AS avg_steps_7d
        FROM daily_steps
        WHERE user_id = :uid AND day >= :week_start
    """), {"uid": str(user_id), "week_start": date.today() - timedelta(days=6)})
    avg_steps_7d = int((steps_row.scalar() or 0))

    # Fetch full habit library so AI can match habits to metrics dynamically
    from app.models import Habit
    lib_rows = await db.execute(
        select(Habit.slug, Habit.label, Habit.impact, Habit.category)
        .order_by(Habit.category, Habit.label)
    )
    habit_library = [
        {"slug": r.slug, "label": r.label, "impact": r.impact, "category": str(r.category)}
        for r in lib_rows.all()
    ]

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
        "user_profile": {
            "gender":         user_gender,
            "age":            user_age,
            "activity_level": user_activity,
        },
        # Pre-computed healthy ranges matching frontend logic — AI must use these, not hardcoded values
        "ideal_ranges": _compute_ideal_ranges(
            user_age, user_gender, user_activity,
            user_height, latest.get("weight_kg"),
        ),
        "active_habits":     active_habits,
        "habit_window_days": habit_window_days,
        "avg_steps_7d":      avg_steps_7d,
        # Full habit library — AI uses this to suggest habits by name and match to metrics
        "habit_library":     habit_library,
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
        "headline": "Your baseline is logged",
        "story": [
            _span(f"You have ", "normal"),
            _span(f"{n} scan{'s' if n > 1 else ''}", "stat", "teal"),
            _span(" on record — your body composition baseline is set. ", "normal"),
            _span("Scan every 14 days", "highlight", "teal"),
            _span(" to start seeing real trends and personalised insights.", "normal"),
        ],
        "highlights": [
            {
                "metric":    "Body fat",
                "direction": "stable",
                "value":     f"{bf}%" if bf else "—",
                "delta":     None,
                "priority":  "medium",
                "note":      [_span("Baseline logged — scan again in 14 days to see change.", "normal")],
            },
            {
                "metric":    "Skeletal muscle",
                "direction": "stable",
                "value":     f"{sm}%" if sm else "—",
                "delta":     None,
                "priority":  "low",
                "note":      [_span("Keep training — next scan will show the difference.", "normal")],
            },
        ],
        "focus": [
            _span("Scan again in ", "normal"),
            _span("14 days", "highlight", "teal"),
            _span(" — that's when trends become visible.", "normal"),
        ],
        "next_milestone":   "Scan in 14 days to unlock your first trend insight.",
        "suggested_habits": [],
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

        # Validate suggested_habits array
        valid_cats = {"nutrition", "fitness", "wellness", "sleep", "mindset"}
        suggested_habits = []
        for sh in (data.get("suggested_habits") or []):
            if isinstance(sh, dict) and sh.get("name"):
                suggested_habits.append({
                    "name":       str(sh.get("name", "")),
                    "in_library": bool(sh.get("in_library", False)),
                    "slug":       str(sh["slug"]) if sh.get("slug") else None,
                    "category":   str(sh.get("category")) if sh.get("category") in valid_cats else "wellness",
                    "frequency":  str(sh.get("frequency", "daily")),
                    "duration":   str(sh["duration"]) if sh.get("duration") else None,
                    "why":        str(sh.get("why", "")),
                    "urgency":    str(sh.get("urgency", "")),
                    "first_step": str(sh.get("first_step", "")),
                })

        payload = {
            "headline":         str(data.get("headline", "")),
            "story":            _validate_spans(data.get("story", [])),
            "highlights":       highlights,
            "focus":            _validate_spans(data.get("focus", [])),
            "next_milestone":   str(data.get("next_milestone", "")),
            "suggested_habits": suggested_habits,
        }

        # Guard: if AI returned empty content, don't cache bad data
        if not payload["headline"] or not highlights:
            logger.warning(f"Body insight AI returned empty output for user {user_id} — raw: {data}")
            return _fallback_body(stats)  # return but do NOT save to cache

    except json.JSONDecodeError as e:
        logger.error(f"Body insight JSON parse error for user {user_id}: {e}")
        return _fallback_body(stats)
    except Exception as e:
        logger.error(f"Body insight AI error for user {user_id}: {e}", exc_info=True)
        return _fallback_body(stats)

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
