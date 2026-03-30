"""
python -m app.db.seed_habits
Run once after migration to populate the habits catalogue.
"""
 
from app.models import Habit
from app.db.session import AsyncSessionLocal
HABITS = [
    # Body
    dict(slug="sleep",               label="Sleep 7–8 hours",               desc="In bed before 11pm — same time every night",            why="Sleep is when muscle repairs, memory consolidates and hormones reset.",                                                   impact="Recovery",     category="Body",      tier="core",   has_counter=False),
    dict(slug="exercise",            label="Exercise 30 min",               desc="Any movement — gym, run, swim, bike",                   why="30 min daily movement is the most effective antidepressant known.",                                                         impact="Longevity",    category="Body",      tier="core",   has_counter=False),
    dict(slug="steps",               label="5,000–10,000 steps",            desc="Start at 5k, build to 10k",                            why="Walking daily lowers cardiovascular disease risk by 40%.",                                                                   impact="Cardio",       category="Body",      tier="core",   has_counter=True,  unit="steps", target=10000),
    dict(slug="water",               label="Drink 2.5L water",              desc="10 glasses across the day",                            why="Even 2% dehydration kills focus, mood and physical output.",                                                                  impact="Hydration",    category="Body",      tier="core",   has_counter=True,  unit="ml",    target=2500),
    dict(slug="sunlight",            label="Morning sunlight 10 min",       desc="Outside within 1hr of waking, no sunglasses",          why="Morning light sets your circadian rhythm and improves sleep that night.",                                                     impact="Energy",       category="Body",      tier="core",   has_counter=False),
    dict(slug="walkaftermeals",      label="Walk after meals 10 min",       desc="Short walk after lunch or dinner",                     why="Post-meal walking reduces blood glucose spikes by up to 30%.",                                                               impact="Metabolism",   category="Body",      tier="core",   has_counter=False),
    dict(slug="veggies",             label="Fill half plate with veg",      desc="Any vegetable — salad, soup or cooked",                why="Most people feel the metabolic shift within 10 days.",                                                                        impact="Nutrition",    category="Body",      tier="core",   has_counter=False),
    dict(slug="nearfareye",          label="Near-far eye training 5 min",   desc="Alternate focus: close object, then distant point",    why="Reduces myopia progression and screen eye strain by up to 30%.",                                                             impact="Vision",       category="Body",      tier="core",   has_counter=False),
    dict(slug="presleepbath",        label="Warm bath before bed",          desc="10 min, 60–90 min before sleep",                      why="Core temperature drop post-bath triggers sleep onset signal.",                                                                 impact="Sleep",        category="Body",      tier="core",   has_counter=False),
    dict(slug="zone2cardio",         label="Zone 2 cardio 30 min",          desc="Conversational pace — bike, walk, swim, jog",          why="Strongest single predictor of longevity and VO2 max.",                                                                        impact="Longevity",    category="Body",      tier="core",   has_counter=False),
    dict(slug="noprocessed",         label="No junk food",                  desc="Fried, packaged or bakery — skip it",                  why="Cut for 7 days and cravings reset. Energy becomes predictable.",                                                              impact="Nutrition",    category="Body",      tier="avoid",  has_counter=False),
    dict(slug="nosmoking",           label="No smoking",                    desc="Zero cigarettes — not even one",                      why="Body begins healing within 20 minutes of stopping.",                                                                           impact="Health",       category="Body",      tier="avoid",  has_counter=False),
    dict(slug="noalcohol",           label="No alcohol",                    desc="Zero drinks — not even one",                          why="Alcohol fragments sleep cycles and depletes B vitamins.",                                                                      impact="Recovery",     category="Body",      tier="avoid",  has_counter=False),
    dict(slug="coldshower",          label="Cold shower 2 min",             desc="End shower on cold — 2 min minimum",                  why="Raises dopamine 250% and norepinephrine 300% for 2–3 hours.",                                                                 impact="Alertness",    category="Body",      tier="growth", has_counter=False),
    dict(slug="proteintarget",       label="Hit daily protein target",      desc="1.6g per kg bodyweight — track it once",              why="Evidence threshold for muscle protein synthesis.",                                                                             impact="Muscle",       category="Body",      tier="growth", has_counter=False),
    # Mind
    dict(slug="meditation",          label="Meditate 10 min",               desc="Quiet sit, breathing app or guided",                  why="Measurably shrinks amygdala and thickens prefrontal cortex in 8 weeks.",                                                      impact="Clarity",      category="Mind",      tier="growth", has_counter=False),
    dict(slug="read",                label="Read 20 minutes",               desc="Books only — not articles or feeds",                  why="Reduces cortisol by 68% in 6 minutes.",                                                                                       impact="Growth",       category="Mind",      tier="growth", has_counter=False),
    dict(slug="learn",               label="Learn 15 min",                  desc="One skill, consistently",                             why="Daily deliberate practice grows myelin, making skills permanent.",                                                             impact="Mastery",      category="Mind",      tier="growth", has_counter=False),
    dict(slug="journal",             label="Gratitude journal",             desc="3 things you're grateful for, 2 min",                 why="Rewires neural pathways — measurable in brain scans after 21 days.",                                                          impact="Mindset",      category="Mind",      tier="growth", has_counter=False),
    dict(slug="breathwork",          label="Breathwork 5 min",              desc="Box breathing, 4-7-8 or Wim Hof",                    why="Activates vagus nerve, drops cortisol in under 90 seconds.",                                                                   impact="Calm",         category="Mind",      tier="growth", has_counter=False),
    dict(slug="deepwork",            label="Deep work block 90 min",        desc="Single task, zero interruptions, phone away",         why="One real block beats 4 distracted hours.",                                                                                    impact="Focus",        category="Mind",      tier="growth", has_counter=False),
    dict(slug="noscreens",           label="No screens 1hr before bed",     desc="Phone down, book or conversation instead",            why="Blue light blocks melatonin for up to 3 hours.",                                                                              impact="Sleep",        category="Mind",      tier="avoid",  has_counter=False),
    dict(slug="nosocialmedia",       label="No social media before 10am",   desc="First 2hrs are for you, not feeds",                   why="Morning dopamine from feeds hijacks motivation for hours.",                                                                    impact="Focus",        category="Mind",      tier="avoid",  has_counter=False),
    dict(slug="proactivelanguage",   label="Use proactive language",        desc="Replace I can't with I choose not to",                why="Language shapes identity and locus of control.",                                                                               impact="Mindset",      category="Mind",      tier="growth", has_counter=False),
    dict(slug="discomfortchallenge", label="Daily discomfort challenge",    desc="One uncomfortable-but-beneficial action today",       why="Deliberate discomfort builds courage as a skill.",                                                                            impact="Resilience",   category="Mind",      tier="growth", has_counter=False),
    dict(slug="visualisation",       label="Visualisation 5 min",           desc="Mental rehearsal of your day or a key goal",          why="Activates the same motor cortex regions as physical practice.",                                                               impact="Performance",  category="Mind",      tier="growth", has_counter=False),
    # Lifestyle
    dict(slug="eatingwindow",        label="Eat within a 10–12hr window",   desc="First meal and last meal within a 10–12hr span",      why="Consistent eating window improves metabolic health and sleep without strict restriction.",                                     impact="Metabolism",   category="Lifestyle", tier="avoid",  has_counter=False),
    dict(slug="caffeine",            label="No caffeine after 2pm",         desc="Coffee, chai, tea — stop by 2pm",                    why="Caffeine is 50% active 6hrs later — a 3pm chai wrecks sleep.",                                                                impact="Sleep",        category="Lifestyle", tier="avoid",  has_counter=False),
    dict(slug="nosugar",             label="No added sugar",                desc="Check labels — sugar hides in everything",            why="21 days resets taste sensitivity permanently.",                                                                               impact="Energy",       category="Lifestyle", tier="avoid",  has_counter=False),
    dict(slug="trackspending",       label="Track daily spending",          desc="Log every purchase — app or note",                   why="Most people underestimate daily spend by 40%.",                                                                               impact="Finance",      category="Lifestyle", tier="growth", has_counter=False),
    dict(slug="preparetomorrow",     label="Prepare tomorrow tonight",      desc="Top 3 tasks + clothes + bag, 5 min",                 why="5-min prep reduces morning cortisol and decision fatigue.",                                                                    impact="Clarity",      category="Lifestyle", tier="growth", has_counter=False),
    dict(slug="timeblock",           label="Time-block your day",           desc="Assign every hour to a task — 5 min planning",       why="Time-blocking increases task completion by 60%.",                                                                             impact="Productivity", category="Lifestyle", tier="growth", has_counter=False),
    dict(slug="daily5goals",         label="Write 5 micro-goals for today", desc="5 small achievable wins — takes 3 min",              why="Small wins trigger dopamine and sustain motivation.",                                                                         impact="Momentum",     category="Lifestyle", tier="growth", has_counter=False),
    dict(slug="stretch",             label="Stretch / mobility 10 min",    desc="Morning or post-work — hips, spine, shoulders",       why="Reduces injury risk by 54%.",                                                                                                 impact="Mobility",     category="Lifestyle", tier="growth", has_counter=False),
    dict(slug="callsomeone",         label="Call someone you care about",   desc="5 min with family or a close friend",                why="Social connection reduces all-cause mortality by 50%.",                                                                        impact="Connection",   category="Lifestyle", tier="growth", has_counter=False),
]


def seed():
    db = AsyncSessionLocal()
    try:
        existing = {h.slug for h in db.query(Habit.slug).all()}
        new = [Habit(**h) for h in HABITS if h["slug"] not in existing]
        if new:
            db.add_all(new)
            db.commit()
            print(f"Seeded {len(new)} habits.")
        else:
            print("Already seeded.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()