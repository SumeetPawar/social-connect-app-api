import asyncio
import asyncpg
import sys
from passlib.context import CryptContext
import os

DB_URL = os.environ.get('DATABASE_URL', 'postgresql://fitness:fitnesspass@localhost:5432/fitnessdb')
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def change_password(user_id, new_password):
    # bcrypt only uses the first 72 bytes; truncate before hashing
    password_bytes = new_password.encode('utf-8')
    if len(password_bytes) > 72:
        print("⚠️  Password is longer than 72 bytes and will be truncated.")
        password_bytes = password_bytes[:72]
    password_hash = pwd_context.hash(password_bytes)
    conn = await asyncpg.connect(DB_URL)
    result = await conn.execute('UPDATE users SET password_hash = $1 WHERE id = $2', password_hash, user_id)
    if result == "UPDATE 0":
        print(f"❌ User with ID {user_id} not found")
    else:
        print(f"✅ Password updated for user {user_id}")
    await conn.close()

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python change_password.py <user_id> <new_password>")
        sys.exit(1)
    user_id = sys.argv[1]
    new_password = sys.argv[2]
    asyncio.run(change_password(user_id, new_password))
