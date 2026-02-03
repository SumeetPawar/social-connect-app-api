import asyncio
import asyncpg

async def test_connection():
    try:
        # Connect to database
        conn = await asyncpg.connect('postgresql://gesadmin:Markmywords%4089@ges-social-pg-prod.postgres.database.azure.com/fitness_tracker')
        
        # Test 1: Check connection
        version = await conn.fetchval('SELECT version()')
        print('✅ Database connected successfully!')
        print(f'PostgreSQL version: {version[:50]}...\n')
        
        # Test 2: List tables
        tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        print(f'✅ Tables in database: {len(tables)}')
        for row in tables:
            print(f'  - {row["tablename"]}')
        
        # Test 3: Count users
        user_count = await conn.fetchval('SELECT COUNT(*) FROM users')
        print(f'\n✅ Users in database: {user_count}')
        
        # Test 4: Check alembic_version (migration status)
        migration = await conn.fetchval('SELECT version_num FROM alembic_version')
        print(f'✅ Current migration version: {migration}')
        
        await conn.close()
        print('\n✅ All database checks passed!')
        
    except Exception as e:
        print(f'❌ Database connection error: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_connection())
