import asyncio
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
# Send a test push notification to a specific user by user_id:
#   python scripts/db_admin_tools.py push_to_user <user_id>

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

# DB_URL = os.environ.get('DATABASE_URL', 'postgresql://fitness:fitnesspass@localhost:5432/fitnessdb')
DB_URL = 'postgresql://gesadmin:Markmywords%4089@ges-social-pg-prod.postgres.database.azure.com/fitness_tracker'

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
    elif cmd == 'list_push_subscriptions':
        asyncio.run(list_push_subscriptions())
    elif cmd == 'delete_push_subscription' and len(sys.argv) == 3:
        asyncio.run(delete_push_subscription(sys.argv[2]))
    elif cmd == 'delete_all_push_subscriptions':
        asyncio.run(delete_all_push_subscriptions())
    elif cmd == 'push_to_user' and len(sys.argv) == 3:
        asyncio.run(push_to_user(sys.argv[2]))
    else:
        print("Invalid command or missing argument.")

