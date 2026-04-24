import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def run():
    async with AsyncSessionLocal() as db:
        # Check subscriptions for all users
        subs = await db.execute(text("""
            SELECT u.name, u.id, COUNT(ps.id) as sub_count
            FROM users u
            LEFT JOIN push_subscriptions ps ON ps.user_id = u.id
            GROUP BY u.name, u.id
            ORDER BY u.name
        """))
        print("=== PUSH SUBSCRIPTIONS PER USER ===")
        for r in subs.mappings():
            print(f"  {r['name']:<20} subs={r['sub_count']}  uid={r['id']}")

        # Check recent errors from push_logs
        errors = await db.execute(text("""
            SELECT pl.job, pl.result, pl.title, pl.sent_at, u.name
            FROM push_logs pl
            JOIN users u ON u.id = pl.user_id
            WHERE pl.result = 'error'
            ORDER BY pl.sent_at DESC
            LIMIT 10
        """))
        print()
        print("=== RECENT ERRORS ===")
        for r in errors.mappings():
            print(f"  {r['name']:<20} job={r['job']}  sent={str(r['sent_at'])[:16]}")

asyncio.run(run())
