import asyncio
import asyncpg
import uuid
import datetime

async def insert_challenge():
    conn = await asyncpg.connect(
    'postgresql://fitness:fitnesspass@localhost:5432/fitnessdb'
    )
    challenge_id = str(uuid.uuid4())
    await conn.execute(
        'INSERT INTO challenges (id, title, period, scope, start_date, end_date, status, description) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
        challenge_id,
        'February Steps Challenge',
        'month',  # Valid values: 'week' or 'month'
        'department',  # Valid values: 'individual', 'team', or 'department'
        datetime.date(2026, 2, 1),
        datetime.date(2026, 2, 28),
        'active',
        "Walk, compete with colleagues, and boost your health. Every step counts—let's make this month active and fun!"
    )
    print(f'Inserted challenge with id: {challenge_id}')
    await conn.close()

if __name__ == '__main__':
    asyncio.run(insert_challenge())
