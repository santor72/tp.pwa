import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import MessengerLinkError, MessengerLinkTokenError
from app.models import MessengerIdentity, MessengerLinkToken, User
from app.repositories import SqlAlchemyMessengerRepository
from app.roles import UserRole, role_for_status
from app.schemas import UserProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LinkResult:
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveIdentity:
    id: UUID
    user_id: UUID
    techportal_user_id: str
    provider: str
    provider_user_id: str
    permissions: dict
    role: UserRole


class MessengerLinkService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], token_ttl_seconds: int, repository: SqlAlchemyMessengerRepository | None = None) -> None:
        self._sessions = sessions
        self._token_ttl_seconds = token_ttl_seconds
        self._repository = repository or SqlAlchemyMessengerRepository()

    async def upsert_user(self, profile: UserProfile) -> User:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            return await self._repository.upsert(session, profile, now)

    async def create(self, user_id: UUID, provider: str) -> LinkResult:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._token_ttl_seconds)
        async with self._sessions.begin() as session:
            identity = await self._repository.active_for_user(session, user_id, provider)
            if identity is not None:
                raise MessengerLinkError("MESSENGER_ALREADY_LINKED", "Telegram уже подключён; сначала отключите его")
            await session.execute(
                update(MessengerLinkToken)
                .where(
                    MessengerLinkToken.user_id == user_id,
                    MessengerLinkToken.provider == provider,
                    MessengerLinkToken.consumed_at.is_(None),
                    MessengerLinkToken.invalidated_at.is_(None),
                )
                .values(invalidated_at=now)
            )
            session.add(MessengerLinkToken(user_id=user_id, provider=provider, token_hash=self._hash(token), expires_at=expires_at))
        logger.info("Messenger link created", extra={"event": "messenger.link.created", "fields": {"user_id": str(user_id), "provider": provider}})
        return LinkResult(token=token, expires_at=expires_at)

    async def consume(
        self,
        provider: str,
        token: str,
        provider_user_id: str,
        username: str | None,
        display_name: str | None,
    ) -> ActiveIdentity:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            link = await session.scalar(
                select(MessengerLinkToken)
                .where(MessengerLinkToken.token_hash == self._hash(token), MessengerLinkToken.provider == provider)
                .with_for_update()
            )
            if link is None or link.consumed_at is not None or link.invalidated_at is not None or self._is_expired(link.expires_at, now):
                raise MessengerLinkTokenError()
            existing_by_user = await session.scalar(
                select(MessengerIdentity).where(
                    MessengerIdentity.user_id == link.user_id,
                    MessengerIdentity.provider == provider,
                    MessengerIdentity.revoked_at.is_(None),
                ).with_for_update()
            )
            if existing_by_user is not None:
                raise MessengerLinkError("MESSENGER_ALREADY_LINKED", "Для пользователя уже есть активная привязка")
            existing_by_provider = await session.scalar(
                select(MessengerIdentity).where(
                    MessengerIdentity.provider == provider,
                    MessengerIdentity.provider_user_id == provider_user_id,
                    MessengerIdentity.revoked_at.is_(None),
                ).with_for_update()
            )
            if existing_by_provider is not None:
                raise MessengerLinkError("MESSENGER_IDENTITY_IN_USE", "Этот Telegram уже связан с другим пользователем")
            identity = MessengerIdentity(
                user_id=link.user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                username=username,
                display_name=display_name,
            )
            session.add(identity)
            link.consumed_at = now
            await session.flush()
            user = await session.get(User, link.user_id)
            assert user is not None
            result = ActiveIdentity(identity.id, user.id, user.techportal_user_id, provider, provider_user_id, user.permissions, role_for_status(user.status))
        logger.info("Messenger linked", extra={"event": "messenger.link.consumed", "fields": {"user_id": str(result.user_id), "provider": provider}})
        return result

    async def active_for_provider_user(self, provider: str, provider_user_id: str) -> ActiveIdentity | None:
        async with self._sessions() as session:
            item = await self._repository.active_for_provider_user(session, provider, provider_user_id)
            if item is None:
                return None
            identity, user = item
            return ActiveIdentity(
                identity.id, user.id, user.techportal_user_id, identity.provider, identity.provider_user_id,
                user.permissions, role_for_status(user.status),
            )

    async def user_id_for_techportal_user(self, techportal_user_id: str | int) -> UUID | None:
        async with self._sessions() as session:
            return await self._repository.find_id_by_techportal_id(session, str(techportal_user_id))

    async def list_active(self, user_id: UUID) -> list[MessengerIdentity]:
        async with self._sessions() as session:
            return await self._repository.list_active(session, user_id)

    async def revoke(self, user_id: UUID, provider: str) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(MessengerIdentity)
                .where(MessengerIdentity.user_id == user_id, MessengerIdentity.provider == provider, MessengerIdentity.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            if result.rowcount == 0:
                raise MessengerLinkError("MESSENGER_NOT_LINKED", "Активная привязка не найдена", 404)
            await session.execute(
                update(MessengerLinkToken)
                .where(MessengerLinkToken.user_id == user_id, MessengerLinkToken.provider == provider, MessengerLinkToken.consumed_at.is_(None), MessengerLinkToken.invalidated_at.is_(None))
                .values(invalidated_at=now)
            )
        logger.info("Messenger link revoked", extra={"event": "messenger.link.revoked", "fields": {"user_id": str(user_id), "provider": provider}})

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _is_expired(expires_at: datetime, now: datetime) -> bool:
        # PostgreSQL returns timezone-aware timestamps; the normalization also
        # keeps the repository portable for the SQLite unit-test database.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now
