from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy import text
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE app_practice_sessions ADD COLUMN rest_seconds INTEGER DEFAULT 15"
            ))
        except Exception:
            pass
        try:
            await conn.execute(text(
                "ALTER TABLE app_users ADD COLUMN is_admin BOOLEAN DEFAULT 0"
            ))
        except Exception:
            pass
        await conn.run_sync(Base.metadata.create_all)
