"""Async SQLAlchemy engine + session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import DatabaseSettings


class Database:
    """Owns the async engine and session factory for a service."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._engine: AsyncEngine = create_async_engine(
            settings.async_url,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            echo=settings.echo,
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    async def session(self) -> AsyncIterator[AsyncSession]:
        """FastAPI dependency yielding a transactional session."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()
