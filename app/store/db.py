"""Database models and helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.engine import make_url
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


class TokenContext(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    token_address: str = Field(primary_key=True)
    symbol: str = Field(index=True)
    source: str | None = Field(default=None)
    base_symbol: str | None = Field(default=None)
    token_name: str | None = Field(default=None)
    pair_address: str | None = Field(default=None)
    url: str | None = Field(default=None)
    chain_id: str | None = Field(default=None)
    saved_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(default_factory=datetime.utcnow)


class TokenWatch(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    token_address: str = Field(primary_key=True)
    token_symbol: str | None = Field(default=None, index=True)
    label: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    session_id: str | None = Field(default=None, index=True)
    tool_calls: str | None = Field(default=None)
    tokens_mentioned: str | None = Field(default=None)
    confidence: float | None = Field(default=None)


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

        url = make_url(self.url)
        if url.get_backend_name() == "sqlite":
            database = url.database
            if database and database != ":memory:":
                Path(database).expanduser().resolve().parent.mkdir(
                    parents=True, exist_ok=True
                )

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
    "TokenContext",
    "TokenWatch",
    "ConversationMessage",
]
