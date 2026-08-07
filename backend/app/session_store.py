import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.errors import ServiceUnavailableError, SessionExpiredError
from app.schemas import SessionData, UserProfile


class SessionStore:
    prefix = "session:"

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def create(
        self,
        user: UserProfile,
        internal_user_id: UUID | None = None,
        upstream_cookies: dict[str, str] | None = None,
    ) -> tuple[str, SessionData]:
        now = datetime.now(UTC)
        session_id = secrets.token_urlsafe(32)
        session = SessionData(
            user=user,
            internal_user_id=internal_user_id,
            upstream_cookies=upstream_cookies or {},
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            absolute_expires_at=now + timedelta(seconds=self._settings.session_absolute_ttl_seconds),
        )
        await self._write(session_id, session)
        return session_id, session

    async def get(self, session_id: str) -> SessionData:
        try:
            raw = await self._redis.get(self._key(session_id))
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc
        if raw is None:
            raise SessionExpiredError()
        try:
            session = SessionData.model_validate_json(raw)
        except ValueError as exc:
            await self.delete(session_id)
            raise SessionExpiredError() from exc

        remaining = int((session.absolute_expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            await self.delete(session_id)
            raise SessionExpiredError()
        await self._expire(session_id, min(remaining, self._settings.session_idle_ttl_seconds))
        return session

    async def delete(self, session_id: str) -> None:
        try:
            await self._redis.delete(self._key(session_id))
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc

    async def _write(self, session_id: str, session: SessionData) -> None:
        ttl = min(self._settings.session_absolute_ttl_seconds, self._settings.session_idle_ttl_seconds)
        try:
            await self._redis.set(self._key(session_id), session.model_dump_json(), ex=ttl)
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc

    async def _expire(self, session_id: str, seconds: int) -> None:
        try:
            await self._redis.expire(self._key(session_id), seconds)
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc

    def _key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"
