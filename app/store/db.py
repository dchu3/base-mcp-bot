"""Database models and helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Subscription(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    router_key: str = Field(primary_key=True)
    lookback_minutes: int = Field(default=30)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class SeenTxn(SQLModel, table=True):
    tx_hash: str = Field(primary_key=True)
    router_key: str
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)


class Database:
    """Lightweight async database wrapper."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._engine: AsyncEngine | None = None
        self._session_maker: sessionmaker | None = None

    def connect(self) -> None:
        """Initialise engine and sessionmaker."""
        if self._engine:
            return

        self._engine = create_async_engine(self.url, echo=False, future=True)
        self._session_maker = sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_models(self) -> None:
        """Create tables if they do not exist."""
        if not self._engine:
            raise RuntimeError("Database engine is not initialised")

        async with self._engine.begin() as conn:  # pragma: no cover - DDL
            await conn.run_sync(SQLModel.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Return an async session context."""
        if not self._session_maker:
            raise RuntimeError("Database session maker is not initialised")

        async with self._session_maker() as session:
            yield session


__all__ = [
    "Database",
    "User",
    "Subscription",
    "Setting",
    "SeenTxn",
]
