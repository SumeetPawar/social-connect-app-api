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
You are the personal health coach inside a wellness app.
The user just saved a body scan. Your output appears immediately on their screen.
Make every word count. Make them feel the scan was worth it. Make them act today.

═══ WRITING RULES ═══
• Use EXACT metric names from the scanner — never rename:
  "Visceral fat", "Body fat", "Skeletal muscle", "BMR", "Metabolic age", "Hydration", "Weight"
• No jargon. No: "cardiovascular", "adiposity", "glycaemic", "insulin sensitivity".
  Instead: "energy levels", "how your body burns fuel", "fat stored around organs".
• Always quote exact numbers: "Level 14", "31%", "1 865 kcal". Never "elevated" or "low" alone.
• Frame in daily life: energy, how clothes fit, sleep quality. Never disease framing.
• No fear. Every sentence points forward.

═══ RANGES ═══
Use "ideal_ranges" — pre-computed for this user's exact age, gender, activity, height.
[low, high]. Outside either = concern. High BMR + high body fat = muscle asset, not a problem.

═══ TRENDS ═══
"prev_scan" = their scan just before latest. "deltas_from_prev" = change since that scan.
Use these to write honest trend_labels per metric:
  delta_from_prev == 0    → "unchanged since last scan"
  delta improves metric   → "improving since last scan" or "down X since last scan"
  delta worsens metric    → "up X since last scan"
  prev_scan is null       → "first scan — no trend yet"

═══ HABITS ═══
"active_habits": current habits. pct_window = % completed over last 15 days.
"habit_library": all in-app habits (slug, label, impact, category).
NOT limited to library — recommend any research-backed habit.

For the #1 concern:
  WIN    — habit ≥70% + metric improved  → celebrate, credit the habit explicitly
  HOLD   — habit ≥70% + unchanged        → reinforce: right habit, give it more time
  PUSH   — habit <70%                    → honest push: log it every single day
  UNLOCK — no relevant habit             → prescribe the single best action

═══ WHAT WORKS (evidence-ranked, highest impact first) ═══
"Stop" habits are as powerful as "do" habits — include them when evidence warrants.

Visceral fat high:
  #1 Sleep 7–9hrs — poor sleep raises cortisol, the primary hormonal driver of visceral fat storage (37% more cortisol at 6hrs sleep vs 8hrs)
  #2 Cut ultra-processed food — strongest single dietary intervention; visceral fat responds in 4–8 weeks
  #3 10–12hr eating window — reduces visceral fat independent of calorie count via circadian metabolism
  #4 HIIT 2–3×/week — burns visceral fat faster than any other exercise modality
  #5 Walk 20 min after biggest meal — blunts post-meal glucose spike, reduces visceral fat accumulation
  #6 Stress reduction (breathwork/meditation) — chronically elevated cortisol directly deposits fat around organs
  #7 Strength training 3×/week — builds muscle that raises resting burn

Body fat high:
  #1 Cut added sugar — fastest dietary route; sugar drives insulin → fat storage mode
  #2 Strength training 3×/week — muscle tissue burns fat 24/7, not just during exercise
  #3 Protein at every meal (30g+) — highest thermic effect of any food, preserves muscle while fat drops
  #4 Sleep 7–9hrs — sleep deprivation raises ghrelin (hunger) and lowers leptin (fullness) — people eat ~350 extra kcal/day on poor sleep
  #5 Soluble fiber daily (oats, legumes, vegetables) — binds dietary fat, feeds gut bacteria that reduce body fat
  #6 No alcohol — liver prioritises alcohol metabolism, fat oxidation shuts down completely while drinking

Skeletal muscle low:
  #1 Strength/resistance training 3–4×/week — only stimulus that directly forces muscle growth (progressive overload)
  #2 Protein 30g at every meal — muscle protein synthesis needs leucine threshold hit per meal, not just daily total
  #3 Sleep 8+hrs — 70% of growth hormone release happens during deep sleep; muscle repairs overnight
  #4 Walk 8,000+ steps/day — daily movement prevents muscle catabolism between training sessions

Metabolic age high:
  #1 Zone 2 cardio 30–45 min, 3–4×/week — strongest evidence for VO2 max improvement, the primary marker of metabolic age
  #2 HIIT 2×/week — improves mitochondrial density (cells that power metabolism), shown to reverse metabolic age 10+ years
  #3 Cut ultra-processed food — directly suppresses mitochondrial efficiency; removing it improves cellular metabolism
  #4 Sleep 8hrs — metabolic restoration happens during deep sleep; chronic poor sleep accelerates metabolic aging
  #5 7–10k steps daily — non-exercise activity thermogenesis (NEAT) keeps metabolism elevated throughout the day

BMR low:
  #1 Strength training 3×/week — each kg of muscle burns ~13 kcal/day at rest; only way to permanently raise BMR
  #2 Sleep 8hrs — sleep deprivation lowers BMR by 5–20% within days
  #3 Adequate protein daily — thermic effect of protein raises metabolic rate; prevents adaptive thermogenesis
  #4 Cut added sugar — chronic high sugar drives insulin resistance which suppresses metabolic rate over time

Hydration low:
  #1 Drink full glass of water before every meal — anchors hydration to existing daily habits
  #2 Start morning with 500ml water — overnight fasting creates dehydration; rehydrating first raises alertness and metabolism
  #3 Reduce ultra-processed/salty snacks — sodium in processed food drives intracellular dehydration
  #4 Electrolyte balance — dehydration is often a sodium/potassium imbalance, not just low water intake

STOP HABITS (removing these often has greater impact than adding a new do-habit):
• No junk/ultra-processed food — top evidence across visceral fat, metabolic age, BMR, hydration
• No added sugar — fastest dietary route to reducing body fat % and stabilising BMR
• No alcohol — shuts down fat oxidation completely; raises body fat % and visceral fat directly
• No late-night eating (after 9pm) — circadian rhythm: calories eaten at night deposit as visceral fat at 3× the rate of same calories eaten midday

Return ONLY valid JSON — no markdown, no code fences.

─── OUTPUT SCHEMA ───

"headline"
  Plain string ≤8 words. Specific. Hook the user.
  Win: "Visceral fat down 2 levels — habit is working"
  Concern: "Visceral fat at Level 14 — one habit will change this"

"focus"
  4 plain strings. No jargon. Short sentences. This is the main coach section.
  {
    "main_focus":       The #1 finding in one sentence. Name the metric and value.
                        "Visceral fat is above the healthy range at Level 14."
    "why_it_matters":   Daily life impact — energy, body feel, not disease.
                        "High visceral fat slows how your body burns fuel and can leave you feeling sluggish after meals."
    "best_next_move":   Single most impactful action. What + how long + how often.
                        "Walk for 10–15 minutes after dinner, every day for the next 2 weeks."
    "expected_benefit": What changes and when. Realistic.
                        "Visceral fat responds quickly to movement — most people see a shift within 3–5 weeks."
  }

"highlights"
  2–4 metric cards. Order: most urgent concern → best win → stable.
  {
    "metric":              exact name — "Visceral fat"|"Body fat"|"Skeletal muscle"|"BMR"|"Hydration"|"Weight"|"Metabolic age"
    "value":               exact value with unit — "Level 14", "31.0%", "1 865 kcal"
    "direction":           "up"|"down"|"stable"
    "delta":               change vs first scan — "-2 levels", "+1.4%", null if 1 scan
    "trend_label":         plain string from trend data — "unchanged since last scan" | "down 2 levels since last scan" | "first scan"
    "priority":            "high"|"medium"|"low"
    "linked_habits":       array of 3–5 habit names (library OR your knowledge) that directly improve this metric
    "linked_steps":        string|null — how daily steps relate, or null
                           "8,000+ steps/day directly supports visceral fat reduction"
    "improvement_horizon": realistic timeframe — "3–6 weeks with daily walks" | "6–10 weeks with strength 3×/week"
  }
  Skip null-value metrics.

"priority_habits"
  Exactly 3. No more. Users act on 3. They ignore 7.
  {
    "do_now":   The single most urgent thing to do TODAY. Concrete, immediate.
                "10-min walk after dinner tonight"
    "do_daily": The one habit to make non-negotiable every day.
                "Eat protein at every meal"
    "avoid":    The one thing actively working against their numbers.
                "Heavy carbs or snacks after 9pm"
  }

"suggested_habits"
  3 to 5 habits. Body-led — chosen holistically across ALL their out-of-range metrics, not one habit per metric.

  THINK FIRST: Which metrics are out of range? List them. Then map each habit to ALL the metrics it moves.
  A single habit often fixes multiple problems — HIIT improves visceral fat, body fat %, BMR and metabolic age.
  Walk after meals improves visceral fat AND hydration/metabolism. Don't list the same benefit twice.

  DEDUPLICATION RULE: If two metrics both benefit from the same habit (e.g. walk after meals helps both
  visceral fat and body fat %), list the habit ONCE and mention both metrics in the "why".
  Never repeat a habit for different metrics.

  PRIORITY ORDER when choosing habits:
  1. Habits that move the MOST out-of-range metrics simultaneously (highest leverage)
  2. Habits that target the user's WORST metric (furthest from ideal range)
  3. Habits the user does NOT currently have active

  INCLUDE STOP/AVOID HABITS when evidence warrants it — "No junk food" or "No added sugar"
  are often the highest-impact intervention available, especially for visceral fat and body fat %.
  Frame them as empowering choices: "Cut added sugar" not "Don't eat sugar".

  NOT limited to habit_library. Recommend any research-backed habit — in or out of library.

  [
    {
      "name":       verb-first — "Walk after dinner", "Eat protein first", "Cut added sugar", "Sleep by 10:30pm"
      "in_library": true if slug exists in habit_library, else false
      "slug":       matching library slug or null
      "category":   "nutrition"|"fitness"|"wellness"|"sleep"|"mindset"
      "frequency":  "daily"|"3×/week"|"weekly"
      "duration":   "20 min"|"10 min"|null

      "why":        ONE sentence. Lead with WHAT THE HABIT DOES, end with the metric it moves.
        The metric values are already shown above — do NOT repeat them here. No "Visceral fat at Level X" openers.
        Formula: [mechanism/what it does] → [which of their metrics improves] → [how fast].
        ✓ "Burns fat stored around organs and lifts your resting metabolism — fastest lever for visceral fat and BMR."
        ✓ "Tells your body to build muscle instead of storing fat — directly raises skeletal muscle %."
        ✓ "Removes the #1 dietary driver of body fat — sugar triggers insulin, which locks fat in storage mode."
        ✓ "Cuts the cortisol spike that deposits fat around organs — bigger impact on visceral fat than most workouts."
        ✗ "Visceral fat at Level 14 and body fat at 30% — ..." (never open with their numbers)
        ✗ "Supports overall health." (too generic — name the specific mechanism)

      "urgency":    ONE sentence. Cost of waiting OR closeness to a win. Tied to their number.
        "Every week without this, visceral fat stays at Level 14 or climbs higher."
        "Two consistent weeks and your next scan will look different."

      "first_step": Exact action for TODAY. Concrete. No vagueness. Most important field.
        ✓ "After dinner tonight, put your shoes on and walk for 15 minutes."
        ✓ "At your next meal, eat protein first — egg, chicken, or nuts — before anything else."
    }
  ]

"next_milestone"
  Plain string ≤15 words. What the next scan will show if they follow the focus now.
  Specific. Tied to their numbers. Reason to scan again in 14 days.
  "Keep the walk habit — next scan could show visceral fat at Level 12."
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

    first  = _scan(scans[0])
    latest = _scan(scans[-1])
    prev   = _scan(scans[-2]) if len(scans) >= 2 else None   # scan just before latest
    total_scans = len(scans)

    # Deltas: latest vs first, and latest vs previous scan
    def _delta(key, base):
        a, b = base.get(key), latest.get(key)
        if a is None or b is None:
            return None
        return round(b - a, 2)

    return {
        "total_scans": total_scans,
        "first_scan":  first,
        "prev_scan":   prev,   # scan before latest — used for trend labels ("down since last scan")
        "latest_scan": latest,
        "deltas_from_first": {
            "weight_kg":           _delta("weight_kg",           first),
            "body_fat_pct":        _delta("body_fat_pct",        first),
            "skeletal_muscle_pct": _delta("skeletal_muscle_pct", first),
            "visceral_fat":        _delta("visceral_fat",        first),
            "metabolic_age":       _delta("metabolic_age",       first),
            "hydration_pct":       _delta("hydration_pct",       first),
        },
        "deltas_from_prev": {
            "weight_kg":           _delta("weight_kg",           prev) if prev else None,
            "body_fat_pct":        _delta("body_fat_pct",        prev) if prev else None,
            "skeletal_muscle_pct": _delta("skeletal_muscle_pct", prev) if prev else None,
            "visceral_fat":        _delta("visceral_fat",        prev) if prev else None,
            "metabolic_age":       _delta("metabolic_age",       prev) if prev else None,
            "hydration_pct":       _delta("hydration_pct",       prev) if prev else None,
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


def _build_body_user_msg(stats: dict) -> str:
    """
    Builds a pre-digested, person-specific brief for the AI instead of a raw JSON dump.
    Forces the AI to reason from THIS person's actual numbers, trends, and behaviour gaps —
    not from generic category rules.
    """
    latest   = stats.get("latest_scan", {})
    ranges   = stats.get("ideal_ranges", {})
    dp       = stats.get("deltas_from_prev", {})
    df       = stats.get("deltas_from_first", {})
    profile  = stats.get("user_profile", {})
    habits   = stats.get("active_habits", [])
    steps    = stats.get("avg_steps_7d", 0)
    library  = stats.get("habit_library", [])
    n_scans  = stats.get("total_scans", 1)

    lines = ["=== THIS USER'S SCAN BRIEF ===\n"]

    # Profile
    age    = profile.get("age")
    gender = profile.get("gender") or "unknown"
    act    = profile.get("activity_level") or "unknown"
    lines.append(f"Profile: {gender}, age {age}, activity={act}, {n_scans} scan(s) on record.\n")

    # Per-metric status — compare against ideal_ranges, compute status
    def status(val, low, high, lower_is_better=False):
        if val is None: return None
        if lower_is_better:
            if val <= high: return "healthy"
            if val <= high * 1.2: return "mildly elevated"
            return "high — needs attention"
        if val < low: return "below range"
        if val <= high: return "healthy"
        return "above range — needs attention"

    metrics = []

    vf = latest.get("visceral_fat")
    if vf is not None:
        vf_range = ranges.get("visceral_fat", [1, 9])
        vf_delta_prev  = dp.get("visceral_fat")
        vf_delta_first = df.get("visceral_fat")
        trend = f"unchanged since last scan" if vf_delta_prev == 0 else \
                f"down {abs(vf_delta_prev)} since last scan" if vf_delta_prev and vf_delta_prev < 0 else \
                f"up {vf_delta_prev} since last scan" if vf_delta_prev else "first scan"
        metrics.append(
            f"Visceral fat: Level {vf} (healthy ≤{vf_range[1]}) — {status(vf, *vf_range, lower_is_better=True)} — trend: {trend}"
            + (f" | since first scan: {vf_delta_first:+}" if vf_delta_first else "")
        )

    bf = latest.get("body_fat_pct")
    if bf is not None:
        bf_range = ranges.get("body_fat_pct", [10, 25])
        bf_delta_prev  = dp.get("body_fat_pct")
        bf_delta_first = df.get("body_fat_pct")
        trend = f"down {abs(bf_delta_prev)}% since last scan" if bf_delta_prev and bf_delta_prev < 0 else \
                f"up {bf_delta_prev}% since last scan" if bf_delta_prev and bf_delta_prev > 0 else \
                "unchanged" if bf_delta_prev == 0 else "first scan"
        metrics.append(
            f"Body fat: {bf}% (healthy {bf_range[0]}–{bf_range[1]}%) — {status(bf, *bf_range)} — trend: {trend}"
            + (f" | since first scan: {bf_delta_first:+}%" if bf_delta_first else "")
        )

    sm = latest.get("skeletal_muscle_pct")
    if sm is not None:
        sm_range = ranges.get("skeletal_muscle_pct", [33, 39])
        sm_delta_prev  = dp.get("skeletal_muscle_pct")
        sm_delta_first = df.get("skeletal_muscle_pct")
        trend = f"up {sm_delta_prev}% since last scan" if sm_delta_prev and sm_delta_prev > 0 else \
                f"down {abs(sm_delta_prev)}% since last scan" if sm_delta_prev and sm_delta_prev < 0 else \
                "stable" if sm_delta_prev == 0 else "first scan"
        metrics.append(
            f"Skeletal muscle: {sm}% (healthy {sm_range[0]}–{sm_range[1]}%) — {status(sm, *sm_range)} — trend: {trend}"
            + (f" | since first scan: {sm_delta_first:+}%" if sm_delta_first else "")
        )

    ma = latest.get("metabolic_age")
    if ma is not None:
        ma_range = ranges.get("metabolic_age", [20, age or 35])
        ma_delta_prev = dp.get("metabolic_age")
        trend = f"improved by {abs(ma_delta_prev)} since last scan" if ma_delta_prev and ma_delta_prev < 0 else \
                f"up {ma_delta_prev} since last scan" if ma_delta_prev and ma_delta_prev > 0 else \
                "stable" if ma_delta_prev == 0 else "first scan"
        metrics.append(
            f"Metabolic age: {ma} (real age {age}, healthy ≤{ma_range[1]}) — "
            + ("older than real age — needs work" if ma > (age or 99) else "on track")
            + f" — trend: {trend}"
        )

    hyd = latest.get("hydration_pct")
    if hyd is not None:
        metrics.append(f"Hydration: {hyd}% — {'good' if hyd >= 50 else 'below ideal'}")

    bmr = latest.get("bmr_kcal")
    if bmr is not None:
        bmr_range = ranges.get("bmr_kcal", [1400, 2000])
        metrics.append(f"BMR: {bmr} kcal (expected range {bmr_range[0]}–{bmr_range[1]} kcal for this profile)")

    lines.append("METRIC STATUS:\n" + "\n".join(f"  • {m}" for m in metrics) + "\n")

    # Active habits
    if habits:
        lines.append("CURRENT HABITS (what they're doing now):")
        for h in habits:
            lines.append(f"  • {h['label']} — {h['pct_window']}% logged over last {stats.get('habit_window_days', 15)} days")
    else:
        lines.append("CURRENT HABITS: none active")
    lines.append("")

    # Steps
    lines.append(f"AVERAGE STEPS (last 7 days): {steps:,}/day")
    lines.append("")

    # Which metrics most need attention — ranked
    concerns = []
    if vf is not None and vf > ranges.get("visceral_fat", [1, 9])[1]:
        concerns.append(f"Visceral fat at Level {vf} (limit {ranges.get('visceral_fat',[1,9])[1]})")
    if bf is not None and bf > ranges.get("body_fat_pct", [10, 25])[1]:
        concerns.append(f"Body fat at {bf}% (limit {ranges.get('body_fat_pct',[10,25])[1]}%)")
    if sm is not None and sm < ranges.get("skeletal_muscle_pct", [33, 39])[0]:
        concerns.append(f"Skeletal muscle at {sm}% (minimum {ranges.get('skeletal_muscle_pct',[33,39])[0]}%)")
    if ma is not None and age and ma > age:
        concerns.append(f"Metabolic age {ma} is {ma - age} years older than real age {age}")

    if concerns:
        lines.append("PRIORITY CONCERNS (most urgent first):\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(concerns)))
    else:
        lines.append("PRIORITY CONCERNS: all metrics within range — focus on maintenance and wins")
    lines.append("")

    # Library slugs for reference
    lines.append(f"HABIT LIBRARY: {len(library)} habits available.")
    lines.append("Use their exact labels. Suggest any habit — in library or not — based on what this person's numbers need.")
    lines.append("")
    lines.append("Now generate the personalised analysis JSON for this specific person.")

    return "\n".join(lines)


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
            _build_body_user_msg(stats),
        )
        data = json.loads(raw)

        # highlights — priority sorted, new fields
        _priority_order = {"high": 0, "medium": 1, "low": 2}
        raw_highlights = [h for h in data.get("highlights", []) if isinstance(h, dict)]
        raw_highlights.sort(key=lambda h: _priority_order.get(h.get("priority", "low"), 2))
        highlights = []
        for h in raw_highlights[:4]:
            highlights.append({
                "metric":               str(h.get("metric", "")),
                "value":                str(h.get("value", "—")),
                "direction":            str(h.get("direction", "stable")),
                "delta":                str(h.get("delta")) if h.get("delta") else None,
                "trend_label":          str(h.get("trend_label", "")),
                "priority":             str(h.get("priority", "low")),
                "linked_habits":        [str(x) for x in (h.get("linked_habits") or [])[:5]],
                "linked_steps":         str(h["linked_steps"]) if h.get("linked_steps") else None,
                "improvement_horizon":  str(h.get("improvement_horizon", "")),
            })

        # focus — structured 4-part object
        raw_focus = data.get("focus") or {}
        focus = {
            "main_focus":       str(raw_focus.get("main_focus", "")),
            "why_it_matters":   str(raw_focus.get("why_it_matters", "")),
            "best_next_move":   str(raw_focus.get("best_next_move", "")),
            "expected_benefit": str(raw_focus.get("expected_benefit", "")),
        }

        # priority_habits — exactly 3 keys
        raw_ph = data.get("priority_habits") or {}
        priority_habits = {
            "do_now":   str(raw_ph.get("do_now", "")),
            "do_daily": str(raw_ph.get("do_daily", "")),
            "avoid":    str(raw_ph.get("avoid", "")),
        }

        # suggested_habits — body-led, 3–5 holistic habits
        # Build ground-truth library map: slug -> label (lowercased for matching)
        library_map = {h["slug"]: h["label"].lower() for h in stats.get("habit_library", [])}
        library_slugs = set(library_map.keys())

        # Keyword aliases: AI paraphrases that map to a specific slug
        _SLUG_ALIASES: dict[str, list[str]] = {
            "noprocessed":   ["ultra-processed", "junk food", "processed food", "packaged food", "cut junk"],
            "nosugar":       ["added sugar", "cut sugar", "no sugar", "reduce sugar"],
            "eatingwindow":  ["eating window", "late-night eating", "time-restricted", "intermittent"],
            "zone2cardio":   ["zone 2", "low intensity cardio", "aerobic"],
            "breathwork":    ["breathwork", "stress reduction", "breathing exercise", "cortisol"],
            "hiit":          ["hiit", "high intensity", "interval training"],
        }

        def _best_library_slug(ai_slug: str | None, habit_name: str) -> str | None:
            """Return the best matching library slug for a suggested habit.
            1. Exact slug match (AI got it right)
            2. Keyword alias match (catches common AI paraphrases)
            3. Fuzzy label match using word overlap + sequence similarity
            """
            import difflib
            if ai_slug and ai_slug in library_slugs:
                return ai_slug
            # Alias check: fast keyword lookup before fuzzy
            name_lower = habit_name.lower()
            for slug, keywords in _SLUG_ALIASES.items():
                if any(kw in name_lower for kw in keywords):
                    return slug
            # Fuzzy: compare habit_name words against every library label
            name_words = set(name_lower.split())
            best_slug, best_score = None, 0.0
            for slug, label in library_map.items():
                label_words = set(label.split())
                overlap = len(name_words & label_words) / max(len(name_words | label_words), 1)
                seq = difflib.SequenceMatcher(None, name_lower, label).ratio()
                score = max(overlap, seq)
                if score > best_score:
                    best_score, best_slug = score, slug
            return best_slug if best_score >= 0.35 else None

        valid_cats = {"nutrition", "fitness", "wellness", "sleep", "mindset"}
        suggested_habits = []
        for sh in (data.get("suggested_habits") or [])[:5]:
            if not (isinstance(sh, dict) and sh.get("name")):
                continue
            matched_slug = _best_library_slug(sh.get("slug") or None, sh.get("name", ""))
            suggested_habits.append({
                "name":       str(sh.get("name", "")),
                "in_library": bool(matched_slug),
                "slug":       matched_slug,
                "category":   str(sh.get("category")) if sh.get("category") in valid_cats else "wellness",
                "frequency":  str(sh.get("frequency", "daily")),
                "duration":   str(sh["duration"]) if sh.get("duration") else None,
                "why":        str(sh.get("why", "")),
                "urgency":    str(sh.get("urgency", "")),
                "first_step": str(sh.get("first_step", "")),
            })

        payload = {
            "headline":         str(data.get("headline", "")),
            "focus":            focus,
            "highlights":       highlights,
            "priority_habits":  priority_habits,
            "suggested_habits": suggested_habits,
            "next_milestone":   str(data.get("next_milestone", "")),
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
