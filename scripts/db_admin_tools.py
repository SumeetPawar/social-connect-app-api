import asyncio
import asyncpg
import sys
import os

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
# List all challenges:
#   python scripts/db_admin_tools.py list_challenges
# List all goals for a user:
#   python scripts/db_admin_tools.py list_goals <user_id>
# Add a goal for a user:
#   python scripts/db_admin_tools.py add_goal <user_id> <metric_key> <day> <value_num> <value_bool>

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

async def list_challenges():
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch('SELECT id, title, period, scope, start_date, end_date, status FROM challenges ORDER BY start_date DESC')
    print('Challenges:')
    for row in rows:
        print(f"{row['id']} | {row['title']} | {row['period']} | {row['scope']} | {row['start_date']} | {row['end_date']} | {row['status']}")
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

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python db_admin_tools.py list_users")
        print("  python db_admin_tools.py delete_user <user_id>")
        print("  python db_admin_tools.py delete_all_users")
        print("  python db_admin_tools.py delete_challenge <challenge_id>")
        print("  python db_admin_tools.py delete_all_challenges")
        print("  python db_admin_tools.py add_challenge <title> <period> <scope> <start_date> <end_date> <status> <description>")
        print("  python db_admin_tools.py list_challenges")
        print("  python db_admin_tools.py list_goals <user_id>")
        print("  python db_admin_tools.py add_goal <user_id> <metric_key> <day> <value_num> <value_bool>")
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
    elif cmd == 'list_challenges':
        asyncio.run(list_challenges())
    elif cmd == 'list_goals' and len(sys.argv) == 3:
        asyncio.run(list_goals(sys.argv[2]))
    elif cmd == 'add_goal' and len(sys.argv) == 7:
        asyncio.run(add_goal(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]))
    else:
        print("Invalid command or missing argument.")
