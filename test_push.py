"""
test_push.py — Run push notification tests directly, no server/token needed.

Usage:
    python test_push.py raw          # send to first valid (non-legacy) subscription in DB
    python test_push.py list         # list all push subscriptions in DB
    python test_push.py clean        # DELETE all legacy FCM subscriptions from DB
    python test_push.py reminder     # run evening step reminder job (9 PM: 0-steps users)
    python test_push.py streak       # run streak-at-risk job (8 PM: streak holders)
    python test_push.py nudge        # run challenge step nudge job (noon/4 PM segments)
    python test_push.py all          # run all jobs
    python test_push.py push_to_user <user_id>  # send test push to all subscriptions for a user
"""

import sys
import asyncio
from app.services.push_notify import send_web_push, PushResult
from app.db.session import AsyncSessionLocal
from app.services.reminder_service import (
    send_step_reminders,
    send_streak_at_risk,
    send_challenge_step_nudges,
)


async def get_first_subscription():
    """Fetch the first push subscription from the DB."""
    from sqlalchemy import select
    from app.models import PushSubscription
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PushSubscription))
        subs = result.scalars().all()
        if not subs:
            print("ERROR: No push subscriptions in DB.")
            print("       Open the app in browser first so it can register a subscription.")
            return None
        sub = subs[0]
        legacy = "⚠️  legacy" if "fcm.googleapis.com/fcm/send/" in sub.endpoint else "✅ modern"
        print(f"Using subscription ({legacy}): {sub.endpoint[:70]}...")
        return {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}


async def test_raw_async():
    sub = await get_first_subscription()
    if not sub:
        return
    print("Sending raw push...")
    result = send_web_push(sub, {
        "title": "🧪 Test Push",
        "body": "Direct push from test_push.py — it works!",
        "url": "/socialapp/steps"
    })
    if result == PushResult.OK:
        print("✅ Sent — check your browser for the notification.")
    elif result == PushResult.EXPIRED:
        print("❌ Subscription expired (404/410). Browser needs to re-subscribe.")
    else:
        print("❌ Push failed (check logs above).")


async def list_subs():
    from sqlalchemy import select
    from app.models import PushSubscription, User
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PushSubscription))
        subs = result.scalars().all()
        if not subs:
            print("No subscriptions found.")
            return
        for sub in subs:
            legacy = "⚠️  LEGACY" if "fcm.googleapis.com/fcm/send/" in sub.endpoint else "✅ OK"
            print(f"{legacy}  user={str(sub.user_id)[:8]}...  endpoint={sub.endpoint[:70]}...")

async def clean_legacy():
    from sqlalchemy import select, delete
    from app.models import PushSubscription
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PushSubscription))
        subs = result.scalars().all()
        legacy = [s for s in subs if "fcm.googleapis.com/fcm/send/" in s.endpoint]
        if not legacy:
            print("No legacy subscriptions found — DB is already clean.")
            return
        await db.execute(
            delete(PushSubscription).where(
                PushSubscription.id.in_([s.id for s in legacy])
            )
        )
        await db.commit()
        print(f"✅ Deleted {len(legacy)} legacy subscription(s).")
        print()
        print("Next steps:")
        print("  1. Open the app in your browser (localhost:3000 or prod URL)")
        print("  2. Wait a few seconds for the new service-worker.js to register")
        print("  3. Check DevTools Console for: [SW] ✅ Subscription synced to backend")
        print("  4. Run: python test_push.py list   (should show ✅ OK endpoints)")
        print("  5. Run: python test_push.py raw")

async def run_job(name: str):
    jobs = {
        "reminder": ("Evening step reminder", send_step_reminders),
        "streak":   ("Streak-at-risk alert", send_streak_at_risk),
        "nudge":    ("Challenge step nudges", send_challenge_step_nudges),
    }
    label, fn = jobs[name]
    print(f"Running job: {label}")
    async with AsyncSessionLocal() as db:
        count = await fn(db)
    print(f"Done — {count} user(s) notified.")


async def run_all():
    for key in ["reminder", "streak", "nudge"]:
        await run_job(key)
        print()


async def push_to_user(user_id):
    """
    Send a test push notification to all push subscriptions for the given user_id.
    Usage:
        python test_push.py push_to_user <user_id>
    """
    from sqlalchemy import select
    from app.models import PushSubscription, User
    async with AsyncSessionLocal() as db:
        # Fetch user email
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        user_email = user.email if user else "(unknown)"
        # Fetch subscriptions
        result = await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
        subs = result.scalars().all()
        if not subs:
            print(f"No push subscriptions found for user {user_id}")
            return
        payload = {
            "title": "🔔 Test Push",
            "body": f"Hello user {user_id}! Email: {user_email}",
        }
        for sub in subs:
            sub_dict = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
            print(f"Sending push to {sub.endpoint[:60]}...")
            result = send_web_push(sub_dict, payload)
            print(f"Result: {result}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "raw"

    if cmd == "raw":
        asyncio.run(test_raw_async())
    elif cmd == "list":
        asyncio.run(list_subs())
    elif cmd == "clean":
        asyncio.run(clean_legacy())
    elif cmd in ("reminder", "streak", "nudge"):
        asyncio.run(run_job(cmd))
    elif cmd == "all":
        asyncio.run(run_all())
    elif cmd == "push_to_user" and len(sys.argv) == 3:
        user_id = sys.argv[2]
        asyncio.run(push_to_user(user_id))
    else:
        print(__doc__)
        sys.exit(1)

