import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def run():
    async with AsyncSessionLocal() as db:
        # All users with their subscription count and endpoint prefix
        subs = await db.execute(text("""
            SELECT u.name, u.id, COUNT(ps.id) as sub_count,
                   LEFT(MAX(ps.endpoint), 60) as endpoint_prefix
            FROM users u
            LEFT JOIN push_subscriptions ps ON ps.user_id = u.id
            GROUP BY u.name, u.id
            ORDER BY sub_count DESC, u.name
        """))
        print("=== ALL USERS + SUBSCRIPTIONS ===")
        for r in subs.mappings():
            print(f"  {r['name']:<25} subs={r['sub_count']}  endpoint={r['endpoint_prefix']}")

        # Total push_logs grouped by result
        totals = await db.execute(text("""
            SELECT result, COUNT(*) as cnt FROM push_logs GROUP BY result
        """))
        print()
        print("=== PUSH_LOGS TOTALS ===")
        for r in totals.mappings():
            print(f"  result={r['result']}  count={r['cnt']}")

        # Check all subscriptions endpoint domains
        endpoints = await db.execute(text("""
            SELECT u.name, LEFT(ps.endpoint, 80) as ep
            FROM push_subscriptions ps
            JOIN users u ON u.id = ps.user_id
        """))
        print()
        print("=== SUBSCRIPTION ENDPOINTS ===")
        for r in endpoints.mappings():
            print(f"  {r['name']}: {r['ep']}")

asyncio.run(run())
