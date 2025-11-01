"""High-level database operations."""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
from sqlmodel import SQLModel

from .db import SeenTxn, Subscription, User


class Repository:
    """CRUD utilities wrapping SQLModel sessions."""

    def __init__(self, session) -> None:
        self.session = session

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
        self.session.merge(subscription)
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

    async def mark_seen(self, tx_hash: str, router_key: str) -> None:
        seen = SeenTxn(tx_hash=tx_hash, router_key=router_key)
        self.session.merge(seen)
        await self.session.commit()

    async def is_seen(self, tx_hash: str) -> bool:
        result = await self.session.execute(
            select(SeenTxn).where(SeenTxn.tx_hash == tx_hash)
        )
        return result.scalar_one_or_none() is not None

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
