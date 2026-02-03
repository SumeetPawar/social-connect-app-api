import asyncio
import asyncpg

async def check_user_schema():
    print(" Connecting to LOCAL database...")
    conn = await asyncpg.connect(
        host='localhost',
        port=5432,
        user='fitness',
        password='fitnesspass',
        database='fitnessdb'
    )
    
    print(" Connected to local database!")
    
    # Check if users table has department field
    columns = await conn.fetch("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'users'
        ORDER BY ordinal_position
    """)
    
    print("\n Users table structure:")
    print("-" * 80)
    dept_field = None
    for col in columns:
        nullable = " Optional" if col['is_nullable'] == 'YES' else " Required"
        print(f"{col['column_name']:20} {col['data_type']:20} {nullable}")
        if 'department' in col['column_name'].lower() or 'dept' in col['column_name'].lower():
            dept_field = col
    
    print("\n" + "=" * 80)
    
    if dept_field:
        print(f"\n Department field found: {dept_field['column_name']}")
        print(f"   Type: {dept_field['data_type']}")
        print(f"   Required: {'No' if dept_field['is_nullable'] == 'YES' else 'Yes'}")
        print(f"   Default: {dept_field['column_default'] or 'None'}")
    else:
        print("\n  No department field found in users table")
    
    # Check foreign key constraints
    fk_constraints = await conn.fetch("""
        SELECT
            tc.constraint_name,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'users' 
        AND tc.constraint_type = 'FOREIGN KEY'
    """)
    
    if fk_constraints:
        print("\n Foreign key constraints:")
        for fk in fk_constraints:
            print(f"   - {fk['column_name']}  {fk['foreign_table_name']}.{fk['foreign_column_name']}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check_user_schema())
