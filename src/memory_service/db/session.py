"""SQLAlchemy async engine + session factory.

Engine is created lazily so test code can swap the URL before first use.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from memory_service.config import Settings, get_settings


class Database:
    """Owns the async engine + sessionmaker. One per process."""

    def __init__(self, url: str) -> None:
        # `pool_pre_ping` saves us from stale connections after pg restart.
        self._engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True, future=True)
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session:
            yield session

    async def ping(self) -> bool:
        """Round-trip the engine. Returns True on success, False on any error."""
        from sqlalchemy import text

        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self._engine.dispose()


_db: Database | None = None


def init_database(settings: Settings | None = None) -> Database:
    global _db
    settings = settings or get_settings()
    _db = Database(settings.database_url)
    return _db


def get_database() -> Database:
    if _db is None:
        return init_database()
    return _db
