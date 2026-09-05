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
    # На PostgreSQL схема управляется Alembic (единая БД с ботом).
    # Legacy-ALTER'ы и повторный create_all здесь ломали транзакцию на PG.
    if settings.DATABASE_URL.startswith("postgresql"):
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
