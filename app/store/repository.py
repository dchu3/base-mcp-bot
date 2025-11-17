"""High-level database operations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import select, text

from .db import (
    ConversationMessage,
    SeenTxn,
    Subscription,
    TokenContext,
    TokenWatch,
    User,
)

TOKEN_CONTEXT_TTL_MINUTES = 60
CONVERSATION_RETENTION_HOURS = 24
CONVERSATION_SESSION_TIMEOUT_MINUTES = 30


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
            result = await self.session.execute(
                text("PRAGMA table_info('tokencontext')")
            )
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

    async def list_watch_tokens(self, user_id: int) -> Iterable[TokenWatch]:
        result = await self.session.execute(
            select(TokenWatch)
            .where(TokenWatch.user_id == user_id)
            .order_by(TokenWatch.created_at)
        )
        return result.scalars().all()

    async def add_watch_token(
        self,
        user_id: int,
        token_address: str,
        token_symbol: str | None = None,
        label: str | None = None,
    ) -> TokenWatch:
        normalized_address = self._normalize_address(token_address)
        result = await self.session.execute(
            select(TokenWatch).where(
                TokenWatch.user_id == user_id,
                TokenWatch.token_address == normalized_address,
            )
        )
        watch = result.scalar_one_or_none()
        if watch:
            if token_symbol is not None:
                watch.token_symbol = token_symbol
            if label is not None:
                watch.label = label
        else:
            watch = TokenWatch(
                user_id=user_id,
                token_address=normalized_address,
                token_symbol=token_symbol,
                label=label,
            )
            self.session.add(watch)
        await self.session.commit()
        await self.session.refresh(watch)
        return watch

    async def remove_watch_token(self, user_id: int, token_address: str) -> None:
        await self.session.execute(
            TokenWatch.__table__.delete().where(
                TokenWatch.user_id == user_id,
                TokenWatch.token_address == self._normalize_address(token_address),
            )
        )
        await self.session.commit()

    async def remove_all_watch_tokens(self, user_id: int) -> None:
        await self.session.execute(
            TokenWatch.__table__.delete().where(TokenWatch.user_id == user_id)
        )
        await self.session.commit()

    async def all_watch_tokens(self) -> Iterable[TokenWatch]:
        result = await self.session.execute(
            select(TokenWatch).order_by(TokenWatch.created_at)
        )
        return result.scalars().all()

    @staticmethod
    def _normalize_address(value: str) -> str:
        trimmed = (value or "").strip()
        if trimmed.startswith("0x"):
            return trimmed.lower()
        return trimmed

    async def save_conversation_message(
        self,
        user_id: int,
        role: str,
        content: str,
        session_id: str | None = None,
        tool_calls: List[Dict[str, Any]] | None = None,
        tokens_mentioned: List[str] | None = None,
        confidence: float | None = None,
    ) -> ConversationMessage:
        """Save a conversation message (user or assistant)."""
        message = ConversationMessage(
            user_id=user_id,
            role=role,
            content=content,
            session_id=session_id,
            tool_calls=json.dumps(tool_calls) if tool_calls else None,
            tokens_mentioned=json.dumps(tokens_mentioned) if tokens_mentioned else None,
            confidence=confidence,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def get_conversation_history(
        self,
        user_id: int,
        limit: int = 10,
        session_id: str | None = None,
    ) -> List[ConversationMessage]:
        """Retrieve recent conversation messages for a user."""
        query = (
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        if session_id:
            query = query.where(ConversationMessage.session_id == session_id)

        result = await self.session.execute(query)
        messages = list(result.scalars().all())
        return list(reversed(messages))

    async def get_or_create_session(
        self,
        user_id: int,
        inactivity_threshold_minutes: int = CONVERSATION_SESSION_TIMEOUT_MINUTES,
    ) -> str:
        """Get current session ID or create new one if inactive."""
        result = await self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        last_message = result.scalar_one_or_none()

        if last_message:
            time_since_last = datetime.utcnow() - last_message.created_at
            if time_since_last.total_seconds() < (inactivity_threshold_minutes * 60):
                return last_message.session_id or str(uuid.uuid4())

        return str(uuid.uuid4())

    async def purge_old_conversations(
        self,
        retention_hours: int = CONVERSATION_RETENTION_HOURS,
    ) -> None:
        """Remove conversation messages older than retention period."""
        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)
        await self.session.execute(
            ConversationMessage.__table__.delete().where(
                ConversationMessage.created_at < cutoff
            )
        )
        await self.session.commit()

    async def clear_conversation_history(self, user_id: int) -> int:
        """Delete all conversation messages for a user.

        Args:
            user_id: The user's ID

        Returns:
            Number of messages deleted
        """
        result = await self.session.execute(
            ConversationMessage.__table__.delete().where(
                ConversationMessage.user_id == user_id
            )
        )
        await self.session.commit()
        return result.rowcount
