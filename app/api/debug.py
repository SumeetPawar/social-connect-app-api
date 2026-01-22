from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db

router = APIRouter(prefix="/debug")

@router.get("/db")
async def db_check(db: AsyncSession = Depends(get_db)):
    res = await db.execute(text("SELECT 1"))
    return {"ok": bool(res.scalar())}
