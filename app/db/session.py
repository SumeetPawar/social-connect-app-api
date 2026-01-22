from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

logger.info(f"Initializing database engine with URL: {settings.DATABASE_URL}")

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=True,  # Log all SQL queries
)

logger.info("Database engine created successfully")

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)