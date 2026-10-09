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
    keepalive_lock_prefix = "techportal-keepalive:"

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

    async def active_sessions(self) -> list[tuple[str, SessionData]]:
        now = datetime.now(UTC)
        active: list[tuple[str, SessionData]] = []
        try:
            async for key in self._redis.scan_iter(match=f"{self.prefix}*"):
                session_id = key.removeprefix(self.prefix)
                raw = await self._redis.get(key)
                if raw is None:
                    continue
                try:
                    session = SessionData.model_validate_json(raw)
                except ValueError:
                    await self._redis.delete(key)
                    continue
                if session.absolute_expires_at <= now or await self._redis.pttl(key) <= 0:
                    await self._redis.delete(key)
                    continue
                active.append((session_id, session))
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc
        return active

    async def claim_upstream_keepalive(self, session_id: str, ttl_seconds: int) -> bool:
        try:
            claimed = await self._redis.eval(
                "if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end "
                "local result = redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[1]) "
                "if result then return 1 else return 0 end",
                2,
                self._key(session_id),
                f"{self.keepalive_lock_prefix}{session_id}",
                ttl_seconds,
            )
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc
        return bool(claimed)

    async def update_upstream_cookies_if_active(self, session_id: str, cookies: dict[str, str]) -> bool:
        script = """
        local raw = redis.call('GET', KEYS[1])
        if not raw then return 0 end
        local ttl = redis.call('PTTL', KEYS[1])
        if ttl <= 0 then return 0 end
        local session = cjson.decode(raw)
        session.upstream_cookies = cjson.decode(ARGV[1])
        redis.call('SET', KEYS[1], cjson.encode(session))
        redis.call('PEXPIRE', KEYS[1], ttl)
        return 1
        """
        try:
            updated = await self._redis.eval(script, 1, self._key(session_id), json.dumps(cookies))
        except RedisError as exc:
            raise ServiceUnavailableError("Хранилище сессий недоступно") from exc
        return bool(updated)

    async def _write(self, session_id: str, session: SessionData) -> None:
        remaining = int((session.absolute_expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise SessionExpiredError()
        ttl = min(remaining, self._settings.session_idle_ttl_seconds)
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
