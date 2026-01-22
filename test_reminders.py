import asyncio
import logging
from app.db.session import AsyncSessionLocal
from app.services.reminder_service import send_step_reminders

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_reminders():
    print("Testing step reminders...")
    async with AsyncSessionLocal() as db:
        try:
            await send_step_reminders(db)
            print("\nTest completed successfully!")
        except Exception as e:
            print(f"\nError during test: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_reminders())
