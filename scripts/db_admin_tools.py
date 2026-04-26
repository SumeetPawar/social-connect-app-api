import asyncio
from datetime import date
import asyncpg
import sys
import os

# Ensure the project root is on sys.path so 'app' can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Usage examples:
# List all users:
#   python scripts/db_admin_tools.py list_users
# Delete a user:
#   python scripts/db_admin_tools.py delete_user <user_id>
# Delete all users:
#   python scripts/db_admin_tools.py delete_all_users
# Delete a challenge:
#   python scripts/db_admin_tools.py delete_challenge <challenge_id>
# Delete all challenges:
#   python scripts/db_admin_tools.py delete_all_challenges
# Add a challenge:
#   python scripts/db_admin_tools.py add_challenge "Title" month department 2026-02-01 2026-02-28 active "Description here"
# Add a challenge for a department:
#   python scripts/db_admin_tools.py add_challenge_for_dept "Title" month department 2026-02-01 2026-02-28 active "Description here" <department_id>
# List all challenges:
#   python scripts/db_admin_tools.py list_challenges
# List all goals for a user:
#   python scripts/db_admin_tools.py list_goals <user_id>
# Add a goal for a user:
#   python scripts/db_admin_tools.py add_goal <user_id> <metric_key> <day> <value_num> <value_bool>   
# Delete body metrics for user on specific date:
#   python scripts/db_admin_tools.py delete_body_metrics <user_id> 2026-03-01
# List all body metrics for a user:
#   python scripts/db_admin_tools.py list_body_metrics <user_id>
# Make admin                    
# python scripts/db_admin_tools.py set-admin 550e8400-e29b-41d4-a716-...  
# Show admins
# python scripts/db_admin_tools.py list-admins                            
# List all departments:
#   python scripts/db_admin_tools.py list_departments
# List all push subscriptions and show which user they are linked to
# List all push subscriptions:
#   python scripts/db_admin_tools.py list_push_subscriptions
# Delete a push subscription by id:
#   python scripts/db_admin_tools.py delete_push_subscription <subscription_id>
# Delete all push subscriptions:
#   python scripts/db_admin_tools.py delete_all_push_subscriptions
# Remove a user from a steps challenge (soft — sets left_at, excluded from leaderboard):
#   python scripts/db_admin_tools.py remove_from_challenge <user_id> <challenge_id>
# Hard-remove a user from a steps challenge (deletes participant row entirely):
#   python scripts/db_admin_tools.py hard_remove_from_challenge <user_id> <challenge_id>
# List challenges a user is enrolled in:
#   python scripts/db_admin_tools.py list_user_challenges <user_id>
# Send a test push notification to a specific user by user_id:
#   python scripts/db_admin_tools.py push_to_user <user_id>
# List all Google Fit connected users + last sync + today's steps:
#   python scripts/db_admin_tools.py list_googlefit
# Manually trigger Google Fit sync for all users:
#   python scripts/db_admin_tools.py sync_googlefit
# Manually trigger Google Fit sync for a single user (syncs TODAY only):
#   python scripts/db_admin_tools.py sync_googlefit <user_id>
# Print raw Google Fit API response + computed steps for a user/date (does NOT save to DB):
#   python scripts/db_admin_tools.py debug_googlefit_raw <user_id> <YYYY-MM-DD>
# Backfill Google Fit steps for every day this month (skips days with 0 steps):
#   python scripts/db_admin_tools.py sync_googlefit_month
#   python scripts/db_admin_tools.py sync_googlefit_month <user_id>

async def list_user_challenges(user_id: str):
    """List all step challenges a user is enrolled in (active and left)."""
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch("""
        SELECT
            cp.challenge_id,
            c.title,
            c.status,
            c.start_date,
            c.end_date,
            cp.left_at
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = $1
        ORDER BY c.start_date DESC
    """, user_id)
    await conn.close()
    if not rows:
        print(f"No challenge enrollments found for user {user_id}")
        return
    print(f"{'challenge_id':<38}  {'title':<30}  {'status':<8}  {'start':<12}  {'end':<12}  left_at")
    print("-" * 120)
    for r in rows:
        left = str(r['left_at']) if r['left_at'] else "(active)"
        print(f"{str(r['challenge_id']):<38}  {str(r['title']):<30}  {r['status']:<8}  {str(r['start_date']):<12}  {str(r['end_date']):<12}  {left}")


async def remove_from_challenge(user_id: str, challenge_id: str):
    """
    Soft-remove a user from a steps challenge by setting left_at = now.
    The participant row is kept but all leaderboard queries filter WHERE left_at IS NULL,
    so they disappear from rankings immediately. Their daily_steps data is preserved.
    """
    conn = await asyncpg.connect(DB_URL)
    # Show current state first
    row = await conn.fetchrow("""
        SELECT cp.left_at, c.title, c.status
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = $1 AND cp.challenge_id = $2
    """, user_id, challenge_id)
    if not row:
        print(f"❌  No enrollment found for user {user_id} in challenge {challenge_id}")
        await conn.close()
        return
    if row['left_at']:
        print(f"⚠️   User already removed (left_at={row['left_at']}) from '{row['title']}'")
        await conn.close()
        return
    result = await conn.execute("""
        UPDATE challenge_participants
        SET left_at = NOW()
        WHERE user_id = $1 AND challenge_id = $2 AND left_at IS NULL
    """, user_id, challenge_id)
    await conn.close()
    print(f"✅  Soft-removed user {user_id} from '{row['title']}' (challenge {challenge_id}).")
    print(f"    left_at set to NOW(). Steps data preserved. User no longer appears on leaderboard.")
    print(f"    Rows updated: {result.split()[-1]}")


async def hard_remove_from_challenge(user_id: str, challenge_id: str):
    """
    Hard-remove: DELETE the challenge_participants row entirely.
    Use this to fully erase an accidental enrollment.
    Daily steps data is NOT deleted — only the participant record.
    """
    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow("""
        SELECT cp.left_at, c.title
        FROM challenge_participants cp
        JOIN challenges c ON c.id = cp.challenge_id
        WHERE cp.user_id = $1 AND cp.challenge_id = $2
    """, user_id, challenge_id)
    if not row:
        print(f"❌  No enrollment found for user {user_id} in challenge {challenge_id}")
        await conn.close()
        return
    confirm = input(f"⚠️  Hard-delete participant row for user {user_id} from '{row['title']}'? [yes/N]: ").strip()
    if confirm.lower() != 'yes':
        print("Aborted.")
        await conn.close()
        return
    result = await conn.execute("""
        DELETE FROM challenge_participants
        WHERE user_id = $1 AND challenge_id = $2
    """, user_id, challenge_id)
    await conn.close()
    print(f"✅  Hard-removed participant row. Rows deleted: {result.split()[-1]}")
    print(f"    Daily steps data for this user is still in daily_steps table.")


async def list_push_subscriptions():
    """
    List all push subscriptions in the DB, showing user_id, endpoint, and creation time.
    If user_id is null or missing, the subscription is not linked to any user.
    """
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT id, user_id, endpoint, created_at FROM push_subscriptions ORDER BY created_at DESC')
    for row in rows:
        print(f"id={row['id']}  user_id={row['user_id']}  endpoint={row['endpoint'][:60]}...  created_at={row['created_at']}")
    await conn.close()

async def delete_push_subscription(subscription_id):
    """
    Delete a push subscription by its id.
    """
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM push_subscriptions WHERE id = $1', subscription_id)
    print(f"Deleted push subscription {subscription_id}: {result}")
    await conn.close()

async def delete_all_push_subscriptions():
    """
    Delete all push subscriptions from the DB.
    """
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM push_subscriptions')
    print(f"Deleted all push subscriptions: {result}")
    await conn.close()

async def list_departments():
    """List all departments."""
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT id, name FROM departments ORDER BY name')
    print(f"Departments ({len(rows)}):")
    for row in rows:
        print(f"{row['id']} | {row['name']}")
    await conn.close()

DB_URL = os.environ.get('DATABASE_URL', 'postgresql://fitness:fitnesspass@localhost:5432/fitnessdb')
# DB_URL = 'postgresql://gesadmin:Markmywords%4089@ges-social-pg-prod.postgres.database.azure.com/fitness_tracker'

# ── Google Fit admin helpers ────────────────────────────────────────────────
async def list_googlefit_connections():
    """
    Show every user who has connected Google Fit, their last sync time,
    and today's recorded step count.
    """
    conn = await asyncpg.connect(DB_URL)
    from datetime import date
    today = date.today()
    rows = await conn.fetch("""
        SELECT
            u.id,
            u.name,
            u.email,
            t.created_at   AS connected_since,
            t.updated_at   AS last_synced,
            t.expires_at,
            COALESCE(ds.steps, 0) AS steps_today
        FROM user_google_fit_tokens t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN daily_steps ds ON ds.user_id = t.user_id AND ds.day = $1
        ORDER BY u.name
    """, today)
    await conn.close()

    if not rows:
        print("No users have connected Google Fit.")
        return

    print(f"\n{'Name':<25} {'Email':<35} {'Connected Since':<22} {'Last Synced':<22} {'Steps Today':>12}")
    print("-" * 120)
    for r in rows:
        print(
            f"{str(r['name'] or ''):<25} "
            f"{r['email']:<35} "
            f"{str(r['connected_since'])[:19]:<22} "
            f"{str(r['last_synced'])[:19]:<22} "
            f"{r['steps_today']:>12}"
        )
    print(f"\nTotal connected: {len(rows)}")


async def trigger_googlefit_sync(user_id: str = None):
    """
    Manually trigger the Google Fit step sync.
    If user_id is provided, syncs only that user.
    Otherwise syncs all connected users.
    """
    from app.services.google_fit import sync_all_users, _refresh_access_token, _fetch_steps_for_date, _upsert_steps
    from app.core.security import decrypt_token, encrypt_token
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import UserGoogleFitToken
    from datetime import datetime, timezone, timedelta
    import httpx

    if user_id:
        # Single-user sync
        conn = await asyncpg.connect(DB_URL)
        row = await conn.fetchrow(
            "SELECT user_id, refresh_token FROM user_google_fit_tokens WHERE user_id = $1",
            user_id
        )
        await conn.close()
        if not row:
            print(f"❌  No Google Fit token found for user {user_id}")
            return

        print(f"Syncing user {user_id}...")
        async with httpx.AsyncClient() as client:
            refresh_token = decrypt_token(row['refresh_token'])
            new_access, new_expires = await _refresh_access_token(client, refresh_token)
            steps = await _fetch_steps_for_date(client, new_access, date.today())
            print(f"  Steps today: {steps}")
            if steps > 0:
                async with AsyncSessionLocal() as db:
                    await _upsert_steps(db, str(row['user_id']), steps)
                print(f"  ✅  Saved {steps} steps")
            else:
                print("  ⚠️  No steps returned from Google Fit (nothing saved)")
            # Update token
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(UserGoogleFitToken).where(UserGoogleFitToken.user_id == str(row['user_id']))
                )
                stored = result.scalar_one_or_none()
                if stored:
                    stored.access_token = encrypt_token(new_access)
                    stored.expires_at = new_expires
                    stored.updated_at = datetime.now(timezone.utc)
                    await db.commit()
            print("  ✅  Token refreshed")
    else:
        print("Triggering full Google Fit sync for all connected users...")
        await sync_all_users()
        print("✅  Sync complete. Check logs for per-user details.")

async def list_users():
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT id, name, email, password_hash FROM users ORDER BY created_at')
    print('Users:')
    for row in rows:
        print(f"{row['id']} | {row['name']} | {row['email']} | {row['password_hash']}")
    await conn.close()

async def delete_user(user_id):
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM users WHERE id = $1', user_id)
    print(f"Deleted user {user_id}: {result}")
    await conn.close()

async def delete_all_users():
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM users')
    print(f"Deleted all users: {result}")
    await conn.close()

async def delete_challenge(challenge_id):
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM challenges WHERE id = $1', challenge_id)
    print(f"Deleted challenge {challenge_id}: {result}")
    await conn.close()

async def delete_all_challenges():
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('DELETE FROM challenges')
    print(f"Deleted all challenges: {result}")
    await conn.close()

async def delete_body_metrics(user_id, date_str):
    """Delete body metrics for a user on a specific date."""
    import datetime
    conn = await asyncpg.connect(DB_URL)
    try:
        recorded_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        result = await conn.execute(
            'DELETE FROM body_metrics WHERE user_id = $1 AND recorded_date = $2',
            user_id, recorded_date
        )
        print(f"Deleted body metrics for user {user_id} on {recorded_date}: {result}")
    except ValueError:
        print(f"❌ Invalid date format. Use YYYY-MM-DD (e.g., 2026-03-01)")
    finally:
        await conn.close()

async def list_body_metrics(user_id):
    """List all body metrics for a specific user."""
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch(
        'SELECT id, recorded_date, weight_kg, bmi, body_fat_pct, visceral_fat, muscle_mass_kg, bone_mass_kg, hydration_pct, protein_pct, bmr_kcal, metabolic_age FROM body_metrics WHERE user_id = $1 ORDER BY recorded_date DESC',
        user_id
    )
    if not rows:
        print(f'No body metrics found for user {user_id}')
    else:
        print(f'Body Metrics for user {user_id}:')
        print(f"{'Date':<12} {'Weight':<8} {'BMI':<6} {'Fat%':<6} {'Visc.Fat':<9} {'Muscle':<8} {'Bone':<6} {'H2O%':<6} {'Protein%':<8} {'BMR':<6} {'MetAge':<6}")
        print('-' * 120)
        for row in rows:
            print(f"{str(row['recorded_date']):<12} {str(row['weight_kg'] or '-'):<8} {str(row['bmi'] or '-'):<6} {str(row['body_fat_pct'] or '-'):<6} {str(row['visceral_fat'] or '-'):<9} {str(row['muscle_mass_kg'] or '-'):<8} {str(row['bone_mass_kg'] or '-'):<6} {str(row['hydration_pct'] or '-'):<6} {str(row['protein_pct'] or '-'):<8} {str(row['bmr_kcal'] or '-'):<6} {str(row['metabolic_age'] or '-'):<6}")
    await conn.close()

async def add_challenge(title, period, scope, start_date, end_date, status, description):
    import uuid, datetime
    conn = await asyncpg.connect(DB_URL)
    challenge_id = str(uuid.uuid4())
    # Parse dates
    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    await conn.execute(
        'INSERT INTO challenges (id, title, period, scope, start_date, end_date, status, description) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
        challenge_id, title, period, scope, start_date, end_date, status, description
    )
    print(f'Inserted challenge with id: {challenge_id}')
    await conn.close()

async def add_challenge_for_dept(title, period, scope, start_date, end_date, status, description, department_id):
    """Add a challenge and link it to a specific department."""
    import uuid, datetime
    conn = await asyncpg.connect(DB_URL)
    challenge_id = str(uuid.uuid4())
    # Parse dates
    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    await conn.execute(
        'INSERT INTO challenges (id, title, period, scope, start_date, end_date, status, description) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
        challenge_id, title, period, scope, start_date, end_date, status, description
    )
    await conn.execute(
        'INSERT INTO challenge_departments (challenge_id, department_id) VALUES ($1, $2)',
        challenge_id, department_id
    )
    print(f'Inserted challenge with id: {challenge_id} for department {department_id}')
    await conn.close()

async def list_challenges():
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT id, title, period, scope, start_date, end_date, status FROM challenges ORDER BY start_date DESC')
    print('Challenges:')
    print(f"{'ID':<37} {'Title':<20} {'Period':<7} {'Scope':<12} {'Start Date':<12} {'End Date':<12} {'Status':<10} {'Departments':<35}")
    print('-' * 155)
    for row in rows:
        # Get departments for this challenge
        dept_rows = await conn.fetch(
            'SELECT d.name FROM departments d JOIN challenge_departments cd ON d.id = cd.department_id WHERE cd.challenge_id = $1 ORDER BY d.name',
            row['id']
        )
        dept_names = ', '.join([d['name'] for d in dept_rows]) if dept_rows else '(company-wide)'
        print(f"{str(row['id']):<37} {str(row['title']):<20} {str(row['period']):<7} {str(row['scope']):<12} {str(row['start_date']):<12} {str(row['end_date']):<12} {str(row['status']):<10} {dept_names:<35}")
    await conn.close()

async def list_goals(user_id):
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT metric_key, day, value_num, value_bool FROM daily_metrics WHERE user_id = $1 ORDER BY day DESC', user_id)
    print(f'Goals for user {user_id}:')
    for row in rows:
        print(f"{row['metric_key']} | {row['day']} | num: {row['value_num']} | bool: {row['value_bool']}")
    await conn.close()

async def add_goal(user_id, metric_key, day, value_num, value_bool):
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(
        'INSERT INTO daily_metrics (user_id, metric_key, day, value_num, value_bool) VALUES ($1, $2, $3, $4, $5)',
        user_id, metric_key, day, value_num, value_bool
    )
    print(f'Added goal for user {user_id} on {day} ({metric_key})')
    await conn.close()

async def set_admin(user_id: str):
    """Set a user as admin by their ID"""
    conn = await asyncpg.connect(DB_URL)

    # Update user role to admin
    result = await conn.execute(
        'UPDATE users SET role = $1 WHERE id = $2',
        'admin',
        user_id
    )

    if result == "UPDATE 0":
        print(f"❌ User with ID {user_id} not found")
    else:
        # Fetch updated user
        user = await conn.fetchrow(
            'SELECT id, name, email, role FROM users WHERE id = $1',
            user_id
        )
        print(f"✅ User set as admin:")
        print(f"   ID: {user['id']}")
        print(f"   Name: {user['name']}")
        print(f"   Email: {user['email']}")
        print(f"   Role: {user['role']}")

    await conn.close()


async def remove_admin(user_id: str):
    """Remove admin role from a user"""
    conn = await asyncpg.connect(DB_URL)

    # Update user role to regular user
    result = await conn.execute(
        'UPDATE users SET role = $1 WHERE id = $2',
        'user',
        user_id
    )

    if result == "UPDATE 0":
        print(f"❌ User with ID {user_id} not found")
    else:
        user = await conn.fetchrow(
            'SELECT id, name, email, role FROM users WHERE id = $1',
            user_id
        )
        print(f"✅ Admin role removed:")
        print(f"   ID: {user['id']}")
        print(f"   Name: {user['name']}")
        print(f"   Email: {user['email']}")
        print(f"   Role: {user['role']}")

    await conn.close()


async def list_admins():
    """List all admin users"""
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch(
        "SELECT id, name, email, role FROM users WHERE role = 'admin' ORDER BY created_at"
    )

    print(f'\n📋 Admin Users ({len(rows)}):')
    for row in rows:
        print(f"   {row['id']} | {row['name']} | {row['email']}")

    await conn.close()


async def push_to_user(user_id):
    """
    Send a test push notification to all push subscriptions for the given user_id.
    """
    from app.services.push_notify import send_web_push
    import json
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = $1', user_id)
    if not rows:
        print(f"No push subscriptions found for user {user_id}")
        await conn.close()
        return
    payload = {
        "title": "🔔 Test Push",
        "body": f"Hello user {user_id}! This is a test notification.",
    }
    for row in rows:
        sub = {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
        print(f"Sending push to {row['endpoint'][:60]}...")
        result = send_web_push(sub, payload)
        print(f"Result: {result}")
    await conn.close()


async def sync_googlefit_month(user_id: str = None):
    """
    Backfill Google Fit steps for every day from the 1st of the current month up to today.
    For each day, fetches from Google Fit and saves to DB if steps > 0 (skips 0-step days).
    If user_id is provided, syncs only that user; otherwise syncs all connected users.
    """
    from app.services.google_fit import _refresh_access_token, _fetch_steps_for_date, _upsert_steps
    from app.core.security import decrypt_token, encrypt_token
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import UserGoogleFitToken
    from datetime import datetime, timezone, timedelta
    import httpx

    today = date.today()
    month_start = today.replace(day=1)

    # Build list of dates: month_start .. today (inclusive)
    days = []
    d = month_start
    while d <= today:
        days.append(d)
        d += timedelta(days=1)

    # Fetch users to process
    conn = await asyncpg.connect(DB_URL)
    if user_id:
        rows = await conn.fetch(
            "SELECT user_id, refresh_token FROM user_google_fit_tokens WHERE user_id = $1",
            user_id
        )
    else:
        rows = await conn.fetch("SELECT user_id, refresh_token FROM user_google_fit_tokens")
    await conn.close()

    if not rows:
        print("No Google Fit users found.")
        return

    print(f"Backfilling {len(days)} days ({month_start} → {today}) for {len(rows)} user(s)...\n")

    for row in rows:
        uid = str(row['user_id'])
        print(f"--- User {uid} ---")
        async with httpx.AsyncClient() as client:
            try:
                refresh_token = decrypt_token(row['refresh_token'])
                new_access, new_expires = await _refresh_access_token(client, refresh_token)
            except Exception as e:
                print(f"  ❌  Token refresh failed: {e}")
                continue

            saved = 0
            skipped = 0
            db_conn = await asyncpg.connect(DB_URL)
            for day in days:
                try:
                    steps = await _fetch_steps_for_date(client, new_access, day)
                    # Check existing DB value
                    existing = await db_conn.fetchval(
                        "SELECT steps FROM daily_steps WHERE user_id = $1 AND day = $2",
                        uid, day
                    )
                    if steps > 0:
                        async with AsyncSessionLocal() as db:
                            await _upsert_steps(db, uid, steps, target_date=day)
                        prev = existing if existing is not None else "none"
                        print(f"  ✅  {day}  →  {steps} steps saved  (was: {prev} in DB)")
                        saved += 1
                    else:
                        prev = existing if existing is not None else "none"
                        print(f"  ⏭️   {day}  →  0 steps from API, skipped  (DB has: {prev})")
                        skipped += 1
                except Exception as e:
                    print(f"  ❌  {day}  →  error: {e}")
            await db_conn.close()

            # Persist refreshed token
            try:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(UserGoogleFitToken).where(UserGoogleFitToken.user_id == uid)
                    )
                    stored = result.scalar_one_or_none()
                    if stored:
                        stored.access_token = encrypt_token(new_access)
                        stored.expires_at = new_expires
                        stored.updated_at = datetime.now(timezone.utc)
                        await db.commit()
            except Exception as e:
                print(f"  ⚠️   Token save failed: {e}")

            print(f"  Summary: {saved} saved, {skipped} skipped (0 steps)\n")

    print("✅  Month backfill complete.")


async def debug_googlefit_raw(user_id: str, day_str: str):
    """
    Print the raw Google Fit API response and computed step count for a user and date.
    Usage: python scripts/db_admin_tools.py debug_googlefit_raw <user_id> <YYYY-MM-DD>
    """
    from app.services.google_fit import _refresh_access_token, _fetch_steps_for_date
    from app.core.security import decrypt_token
    import httpx
    import json
    from datetime import datetime
    # Fetch refresh token
    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT user_id, refresh_token FROM user_google_fit_tokens WHERE user_id = $1",
        user_id
    )
    await conn.close()
    if not row:
        print(f"❌  No Google Fit token found for user {user_id}")
        return
    try:
        target_date = datetime.strptime(day_str, "%Y-%m-%d").date()
    except Exception as e:
        print(f"❌  Invalid date: {e}")
        return
    async with httpx.AsyncClient() as client:
        refresh_token = decrypt_token(row['refresh_token'])
        new_access, _ = await _refresh_access_token(client, refresh_token)
        # Patch _fetch_steps_for_date to print the raw response
        from app.services import google_fit as gf
        _IST = gf.timezone(gf.timedelta(hours=5, minutes=30))
        start_dt = gf.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=_IST)
        next_day  = target_date + gf.timedelta(days=1)
        end_dt    = gf.datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=_IST)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        resp = await client.post(
            gf._GOOGLE_FIT_AGGREGATE_URL,
            headers={"Authorization": f"Bearer {new_access}"},
            json={
                "aggregateBy": [{"dataTypeName": "com.google.step_count.delta"}],
                "bucketByTime": {"durationMillis": 86400000},
                "startTimeMillis": start_ms,
                "endTimeMillis": end_ms,
            },
            timeout=15,
        )
        print("\n--- RAW GOOGLE FIT RESPONSE ---")
        print(json.dumps(resp.json(), indent=2))
        print("--- END RESPONSE ---\n")
        # Now compute steps as usual
        total = 0
        for bucket in resp.json().get("bucket", []):
            for dataset in bucket.get("dataset", []):
                for point in dataset.get("point", []):
                    for val in point.get("value", []):
                        total += val.get("intVal", 0)
        print(f"Computed steps for {target_date}: {total}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python db_admin_tools.py list_users")
        print("  python db_admin_tools.py delete_user <user_id>")
        print("  python db_admin_tools.py delete_all_users")
        print("  python db_admin_tools.py delete_challenge <challenge_id>")
        print("  python db_admin_tools.py delete_all_challenges")
        print("  python db_admin_tools.py add_challenge <title> <period> <scope> <start_date> <end_date> <status> <description>")
        print("  python db_admin_tools.py add_challenge_for_dept <title> <period> <scope> <start_date> <end_date> <status> <description> <department_id>")
        print("  python db_admin_tools.py list_challenges")
        print("  python db_admin_tools.py list_goals <user_id>")
        print("  python db_admin_tools.py add_goal <user_id> <metric_key> <day> <value_num> <value_bool>")
        print("  python db_admin_tools.py delete_body_metrics <user_id> <date>")
        print("  python db_admin_tools.py list_body_metrics <user_id>")
        print("  python db_admin_tools.py set-admin <id>    - Make user admin")
        print("  python db_admin_tools.py remove-admin <id> - Remove admin role")
        print("  python db_admin_tools.py list-admins       - List all admins")
        print("  python db_admin_tools.py list_departments  - List all departments")
        print("  python db_admin_tools.py push_to_user <user_id> - Send test push to user")
        print("  python db_admin_tools.py debug_googlefit_raw <user_id> <YYYY-MM-DD> - Print raw Google Fit API response for a user/date")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'list_users':
        asyncio.run(list_users())
    elif cmd == 'delete_user' and len(sys.argv) == 3:
        asyncio.run(delete_user(sys.argv[2]))
    elif cmd == 'delete_all_users':
        asyncio.run(delete_all_users())
    elif cmd == 'delete_challenge' and len(sys.argv) == 3:
        asyncio.run(delete_challenge(sys.argv[2]))
    elif cmd == 'delete_all_challenges':
        asyncio.run(delete_all_challenges())
    elif cmd == 'add_challenge' and len(sys.argv) == 9:
        # Usage: add_challenge <title> <period> <scope> <start_date> <end_date> <status> <description>
        asyncio.run(add_challenge(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7], sys.argv[8]
        ))
    elif cmd == 'add_challenge_for_dept' and len(sys.argv) == 10:
        # Usage: add_challenge_for_dept <title> <period> <scope> <start_date> <end_date> <status> <description> <department_id>
        asyncio.run(add_challenge_for_dept(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7], sys.argv[8], sys.argv[9]
        ))
    elif cmd == 'list_challenges':
        asyncio.run(list_challenges())
    elif cmd == 'list_goals' and len(sys.argv) == 3:
        asyncio.run(list_goals(sys.argv[2]))
    elif cmd == 'add_goal' and len(sys.argv) == 7:
        asyncio.run(add_goal(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]))
    elif cmd == 'delete_body_metrics' and len(sys.argv) == 4:
        asyncio.run(delete_body_metrics(sys.argv[2], sys.argv[3]))
    elif cmd == "set-admin":
        if len(sys.argv) < 3:
            print("❌ Please provide user ID")
            sys.exit(1)
        asyncio.run(set_admin(sys.argv[2]))
    elif cmd == "remove-admin":
        if len(sys.argv) < 3:
            print("❌ Please provide user ID")
            sys.exit(1)
        asyncio.run(remove_admin(sys.argv[2]))
    elif cmd == "list-admins":
        asyncio.run(list_admins())
    elif cmd == 'list_departments':
        asyncio.run(list_departments())
    elif cmd == 'list_user_challenges' and len(sys.argv) == 3:
        asyncio.run(list_user_challenges(sys.argv[2]))
    elif cmd == 'remove_from_challenge' and len(sys.argv) == 4:
        asyncio.run(remove_from_challenge(sys.argv[2], sys.argv[3]))
    elif cmd == 'hard_remove_from_challenge' and len(sys.argv) == 4:
        asyncio.run(hard_remove_from_challenge(sys.argv[2], sys.argv[3]))
    elif cmd == 'list_push_subscriptions':
        asyncio.run(list_push_subscriptions())
    elif cmd == 'delete_push_subscription' and len(sys.argv) == 3:
        asyncio.run(delete_push_subscription(sys.argv[2]))
    elif cmd == 'delete_all_push_subscriptions':
        asyncio.run(delete_all_push_subscriptions())
    elif cmd == 'push_to_user' and len(sys.argv) == 3:
        asyncio.run(push_to_user(sys.argv[2]))
    elif cmd == 'list_googlefit':
        asyncio.run(list_googlefit_connections())
    elif cmd == 'sync_googlefit':
        user_arg = sys.argv[2] if len(sys.argv) == 3 else None
        asyncio.run(trigger_googlefit_sync(user_arg))
    elif cmd == 'debug_googlefit_raw' and len(sys.argv) == 4:
        asyncio.run(debug_googlefit_raw(sys.argv[2], sys.argv[3]))
    elif cmd == 'sync_googlefit_month':
        user_arg = sys.argv[2] if len(sys.argv) == 3 else None
        asyncio.run(sync_googlefit_month(user_arg))
    else:
        print("Invalid command or missing argument.")

