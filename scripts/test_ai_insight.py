"""
Standalone script to generate and print an AI insight for any user.

Usage (from the project root):
    python scripts/test_ai_insight.py
    python scripts/test_ai_insight.py --user-id <uuid>
    python scripts/test_ai_insight.py --provider azure
    python scripts/test_ai_insight.py --stats-only      # skip AI, just show raw stats
"""
import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

# Make sure project root is on the path so app.* imports work
# Azure OpenAI config is read from .env / environment variables (see app/core/config.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── ANSI terminal rendering ───────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_COLOR  = {
    "purple": "\033[35m",
    "green":  "\033[32m",
    "orange": "\033[33m",
    "rose":   "\033[31m",
    "teal":   "\033[36m",
}


def _render_spans(spans: list) -> str:
    out = ""
    for span in spans:
        text  = span.get("text", "")
        style = span.get("style", "normal")
        color = span.get("color")
        prefix = ""
        if color and color in _COLOR:
            prefix += _COLOR[color]
        if style in ("stat", "milestone"):
            prefix += _BOLD
        out += prefix + text + (_RESET if prefix else "")
    return out


def _render_insight(insight: dict) -> None:
    badge = insight.get("badge", "")
    print(f"  {_BOLD}[{badge}]{_RESET}\n")
    print(f"  {_render_spans(insight.get('segments', []))}")
    print(f"  {_render_spans(insight.get('detail', []))}\n")
    hook = insight.get("hook", "")
    print(f"  {_COLOR['teal']}↳ {hook}{_RESET}\n")


# ── DB / AI helpers ───────────────────────────────────────────────────────────

from app.db.session import AsyncSessionLocal
from app.services.ai_insight import _collect_stats, get_home_insight
from sqlalchemy import text


async def pick_user(db, user_id: str | None) -> str:
    if user_id:
        return user_id
    # Auto-pick the first user that has any data
    row = await db.execute(text("""
        SELECT u.id FROM users u
        JOIN daily_steps ds ON ds.user_id = u.id
        ORDER BY ds.day DESC
        LIMIT 1
    """))
    result = row.scalar()
    if not result:
        print("No users with step data found in the database.")
        sys.exit(1)
    return str(result)


async def main(user_id: str | None, provider: str | None, stats_only: bool):
    # Override provider without touching .env
    if provider:
        os.environ["AI_PROVIDER"] = provider
        # Re-import config after env override so settings picks it up
        from importlib import reload
        import app.core.config as cfg
        reload(cfg)
        from app.core import config as cfg2
        cfg2.settings.__dict__["AI_PROVIDER"] = provider

    async with AsyncSessionLocal() as db:
        uid = await pick_user(db, user_id)
        print(f"\n{'─'*54}")
        print(f"  User ID : {uid}")
        print(f"  Provider: {os.environ.get('AI_PROVIDER', 'anthropic')}")
        print(f"{'─'*54}\n")

        # ── Raw stats ────────────────────────────────────────────────────────
        print("Collecting 7-day stats …")
        stats = await _collect_stats(db, uid)
        print("\n📊  Raw Stats")
        print(json.dumps(stats, indent=2))

        if stats_only:
            return

        # ── AI Insight ───────────────────────────────────────────────────────
        print(f"\n🤖  Calling AI ({os.environ.get('AI_PROVIDER', 'anthropic')}) …\n")
        insight = await get_home_insight(db, uid)

        print("✅  AI Insight\n")
        _render_insight(insight)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview AI home insight for a user")
    parser.add_argument("--user-id",    default=None, help="UUID of the user (auto-picks if omitted)")
    parser.add_argument("--provider",   default=None, choices=["anthropic", "azure"],
                        help="Override AI_PROVIDER from .env")
    parser.add_argument("--stats-only", action="store_true",
                        help="Print raw stats without calling the AI")
    args = parser.parse_args()

    asyncio.run(main(args.user_id, args.provider, args.stats_only))
