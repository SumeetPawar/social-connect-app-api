from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
import logging

logger = logging.getLogger(__name__)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    logger.info("Attempting to get database session")
    try:
        async with AsyncSessionLocal() as session:
            logger.info("Database session created successfully")
            yield session
            logger.info("Database session closed")
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}", exc_info=True)
        raise