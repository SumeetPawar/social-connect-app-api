import asyncio
import asyncpg
import uuid
from datetime import datetime

async def create_gesbms_department():
    conn = await asyncpg.connect(
        host='localhost',
        port=5432,
        user='fitness',
        password='fitnesspass',
        database='fitnessdb'
    )
    
    # Check if exists
    existing = await conn.fetchval("SELECT id FROM departments WHERE name = 'GESBMS'")
    
    if existing:
        print(f"GESBMS exists: {existing}")
        print(f"DEFAULT_DEPARTMENT_ID={existing}")
        await conn.close()
        return
    
    # Create department
    dept_id = str(uuid.uuid4())
    
    result = await conn.execute(
        'INSERT INTO departments (id, name, created_at) VALUES (\, \, \)',
        uuid.UUID(dept_id),
        'GESBMS',
        datetime.now()
    )
    
    print(f'Created GESBMS: {dept_id}')
    print(f'DEFAULT_DEPARTMENT_ID={dept_id}')
    
    await conn.close()

asyncio.run(create_gesbms_department())
