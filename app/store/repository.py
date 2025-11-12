"""High-level database operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional, Sequence

from sqlalchemy import select, text
from sqlmodel import SQLModel

from .db import SeenTxn, Subscription, TokenContext, User

TOKEN_CONTEXT_TTL_MINUTES = 60


class Repository:
    """CRUD utilities wrapping SQLModel sessions."""

    def __init__(self, session) -> None:
        self.session = session

    _token_context_schema_ok: bool = False

    async def get_or_create_user(self, chat_id: int) -> User:
        result = await self.session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if user:
            return user

        user = User(chat_id=chat_id)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def list_subscriptions(self, user_id: int) -> Iterable[Subscription]:
        result = await self.session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return result.scalars().all()

    async def all_subscriptions(self) -> Iterable[Subscription]:
        result = await self.session.execute(select(Subscription))
        return result.scalars().all()

    async def add_subscription(
        self,
        user_id: int,
        router_key: str,
        lookback_minutes: int,
    ) -> Subscription:
        subscription = Subscription(
            user_id=user_id,
            router_key=router_key,
            lookback_minutes=lookback_minutes,
        )
        await self.session.merge(subscription)
        await self.session.commit()
        return subscription

    async def remove_subscription(self, user_id: int, router_key: str) -> None:
        await self.session.execute(
            Subscription.__table__.delete().where(
                Subscription.user_id == user_id,
                Subscription.router_key == router_key,
            )
        )
        await self.session.commit()

    async def remove_all_subscriptions(self, user_id: int) -> None:
        await self.session.execute(
            Subscription.__table__.delete().where(Subscription.user_id == user_id)
        )
        await self.session.commit()

    async def mark_seen(self, tx_hash: str, router_key: str) -> None:
        seen = SeenTxn(tx_hash=tx_hash, router_key=router_key)
        await self.session.merge(seen)
        await self.session.commit()

    async def is_seen(self, tx_hash: str) -> bool:
        result = await self.session.execute(
            select(SeenTxn).where(SeenTxn.tx_hash == tx_hash)
        )
        return result.scalar_one_or_none() is not None

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def save_token_context(
        self,
        user_id: int,
        tokens: Sequence[Mapping[str, str]],
        source: str | None = None,
        ttl_minutes: int = TOKEN_CONTEXT_TTL_MINUTES,
    ) -> None:
        """Persist recent token data for follow-up natural-language queries."""
        await self._ensure_token_context_schema()
        await self.session.execute(
            TokenContext.__table__.delete().where(TokenContext.user_id == user_id)
        )
        if not tokens:
            await self.session.commit()
            return

        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=ttl_minutes)
        rows = []
        for token in tokens:
            address = token.get("address") or token.get("tokenAddress")
            symbol = token.get("symbol")
            if not address or not symbol:
                continue
            rows.append(
                TokenContext(
                    user_id=user_id,
                    token_address=str(address),
                    symbol=str(symbol),
                    source=token.get("source") or source,
                    base_symbol=token.get("baseSymbol"),
                    token_name=token.get("name"),
                    pair_address=token.get("pairAddress"),
                    url=token.get("url"),
                    chain_id=token.get("chainId"),
                    saved_at=now,
                    expires_at=expires_at,
                )
            )
        if rows:
            self.session.add_all(rows)
        await self.session.commit()

    async def list_active_token_context(self, user_id: int) -> Iterable[TokenContext]:
        """Return unexpired token context for a user."""
        await self._ensure_token_context_schema()
        result = await self.session.execute(
            select(TokenContext).where(
                TokenContext.user_id == user_id,
                TokenContext.expires_at > datetime.utcnow(),
            )
        )
        return result.scalars().all()

    async def purge_expired_token_context(self) -> None:
        """Remove expired token context across all users."""
        await self._ensure_token_context_schema()
        await self.session.execute(
            TokenContext.__table__.delete().where(
                TokenContext.expires_at <= datetime.utcnow()
            )
        )
        await self.session.commit()

    async def _ensure_token_context_schema(self) -> None:
        """Add new token context columns if missing."""
        if getattr(self, "_token_context_schema_ok", False):
            return
        try:
            result = await self.session.execute(text("PRAGMA table_info('tokencontext')"))
        except Exception:
            # Table may not exist yet; init_models will create it later.
            self._token_context_schema_ok = True
            return
        existing = {row[1] for row in result}
        alters = []
        if "base_symbol" not in existing:
            alters.append("ALTER TABLE tokencontext ADD COLUMN base_symbol VARCHAR")
        if "token_name" not in existing:
            alters.append("ALTER TABLE tokencontext ADD COLUMN token_name VARCHAR")
        for stmt in alters:
            await self.session.execute(text(stmt))
        if alters:
            await self.session.commit()
        self._token_context_schema_ok = True
