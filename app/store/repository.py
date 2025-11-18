"""High-level database operations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select, text

from .db import ConversationMessage, TokenContext, User

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

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def save_token_context(
        self,
        user_id: int,
        tokens: List[Dict[str, str]],
    ) -> None:
        """Store or update token context entries (address, symbol, pair, etc.)."""
        if not tokens:
            return

        await self._ensure_token_context_schema()

        now = datetime.utcnow()
        expires = now + timedelta(minutes=TOKEN_CONTEXT_TTL_MINUTES)

        for entry in tokens:
            address = entry.get("address")
            if not address or not isinstance(address, str):
                continue

            symbol = entry.get("symbol") or ""
            source = entry.get("source")
            base_symbol = entry.get("baseSymbol")
            token_name = entry.get("name")
            pair_address = entry.get("pairAddress")
            url = entry.get("url")
            chain_id = entry.get("chainId")

            existing_result = await self.session.execute(
                select(TokenContext).where(
                    TokenContext.user_id == user_id,
                    TokenContext.token_address == address,
                )
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                existing.symbol = symbol
                existing.source = source
                existing.base_symbol = base_symbol
                existing.token_name = token_name
                existing.pair_address = pair_address
                existing.url = url
                existing.chain_id = chain_id
                existing.saved_at = now
                existing.expires_at = expires
            else:
                ctx = TokenContext(
                    user_id=user_id,
                    token_address=address,
                    symbol=symbol,
                    source=source,
                    base_symbol=base_symbol,
                    token_name=token_name,
                    pair_address=pair_address,
                    url=url,
                    chain_id=chain_id,
                    saved_at=now,
                    expires_at=expires,
                )
                self.session.add(ctx)

        await self.session.commit()

    async def list_active_token_context(self, user_id: int) -> Iterable[TokenContext]:
        """Return non-expired token context entries for a user."""
        await self._ensure_token_context_schema()
        now = datetime.utcnow()
        result = await self.session.execute(
            select(TokenContext).where(
                TokenContext.user_id == user_id,
                TokenContext.expires_at > now,
            )
        )
        return result.scalars().all()

    async def purge_expired_token_context(self) -> None:
        """Delete token context rows that have expired."""
        await self._ensure_token_context_schema()
        now = datetime.utcnow()
        await self.session.execute(
            TokenContext.__table__.delete().where(TokenContext.expires_at <= now)
        )
        await self.session.commit()

    async def _ensure_token_context_schema(self) -> None:
        """Ensure base_symbol, pair_address columns exist on TokenContext."""
        if self._token_context_schema_ok:
            return

        try:
            await self.session.execute(
                text(
                    "SELECT base_symbol, token_name, pair_address, url, chain_id FROM tokencontext LIMIT 1"
                )
            )
            self._token_context_schema_ok = True
        except Exception:
            await self.session.execute(
                text("ALTER TABLE tokencontext ADD COLUMN base_symbol TEXT")
            )
            await self.session.execute(
                text("ALTER TABLE tokencontext ADD COLUMN token_name TEXT")
            )
            await self.session.execute(
                text("ALTER TABLE tokencontext ADD COLUMN pair_address TEXT")
            )
            await self.session.execute(
                text("ALTER TABLE tokencontext ADD COLUMN url TEXT")
            )
            await self.session.execute(
                text("ALTER TABLE tokencontext ADD COLUMN chain_id TEXT")
            )
            await self.session.commit()
            self._token_context_schema_ok = True

    @staticmethod
    def _normalize_address(value: str) -> str:
        """Ensure address is lowercase and stripped."""
        return value.strip().lower()

    async def save_conversation_message(
        self,
        user_id: int,
        role: str,
        content: str,
        session_id: str | None = None,
        tool_calls: List[str] | None = None,
        tokens_mentioned: List[str] | None = None,
        confidence: float | None = None,
    ) -> None:
        """Save a conversation message."""
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

    async def get_conversation_history(
        self, user_id: int, limit: int = 10
    ) -> List[ConversationMessage]:
        """Retrieve recent conversation messages for a user."""
        result = await self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        return list(reversed(messages))

    async def get_or_create_session(self, user_id: int) -> str:
        """Get current session ID or create new one if timed out."""
        result = await self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        last_message = result.scalar_one_or_none()

        if last_message and last_message.session_id:
            timeout = timedelta(minutes=CONVERSATION_SESSION_TIMEOUT_MINUTES)
            if datetime.utcnow() - last_message.created_at < timeout:
                return last_message.session_id

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
