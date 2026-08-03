"""Persistence ports and their SQLAlchemy implementation for messenger data."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MessengerIdentity, User
from app.schemas import UserProfile


class UserRepository(Protocol):
    async def upsert(self, session: AsyncSession, profile: UserProfile, now: datetime) -> User: ...
    async def find_id_by_techportal_id(self, session: AsyncSession, techportal_user_id: str) -> UUID | None: ...


class MessengerIdentityRepository(Protocol):
    async def active_for_provider_user(self, session: AsyncSession, provider: str, provider_user_id: str) -> tuple[MessengerIdentity, User] | None: ...
    async def active_for_user(self, session: AsyncSession, user_id: UUID, provider: str) -> MessengerIdentity | None: ...
    async def list_active(self, session: AsyncSession, user_id: UUID) -> list[MessengerIdentity]: ...


class SqlAlchemyMessengerRepository:
    async def upsert(self, session: AsyncSession, profile: UserProfile, now: datetime) -> User:
        user = await session.scalar(select(User).where(User.techportal_user_id == str(profile.id)).with_for_update())
        values = {
            "email": profile.email,
            "first_name": profile.first_name,
            "status": profile.status,
            "permissions": profile.user_permissions,
            "last_login_at": now,
        }
        if user is None:
            user = User(techportal_user_id=str(profile.id), **values)
            session.add(user)
        else:
            for name, value in values.items():
                setattr(user, name, value)
        await session.flush()
        return user

    async def find_id_by_techportal_id(self, session: AsyncSession, techportal_user_id: str) -> UUID | None:
        return await session.scalar(select(User.id).where(User.techportal_user_id == techportal_user_id))

    async def active_for_provider_user(self, session: AsyncSession, provider: str, provider_user_id: str) -> tuple[MessengerIdentity, User] | None:
        row = await session.execute(
            select(MessengerIdentity, User)
            .join(User, User.id == MessengerIdentity.user_id)
            .where(MessengerIdentity.provider == provider, MessengerIdentity.provider_user_id == provider_user_id, MessengerIdentity.revoked_at.is_(None))
        )
        return row.first()

    async def active_for_user(self, session: AsyncSession, user_id: UUID, provider: str) -> MessengerIdentity | None:
        return await session.scalar(
            select(MessengerIdentity).where(MessengerIdentity.user_id == user_id, MessengerIdentity.provider == provider, MessengerIdentity.revoked_at.is_(None))
        )

    async def list_active(self, session: AsyncSession, user_id: UUID) -> list[MessengerIdentity]:
        result = await session.scalars(select(MessengerIdentity).where(MessengerIdentity.user_id == user_id, MessengerIdentity.revoked_at.is_(None)))
        return list(result)
