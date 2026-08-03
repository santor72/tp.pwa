import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import TelegramObject
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class TelegramAccessService:
    """Fail-closed membership check with a config-versioned Redis cache."""

    def __init__(self, redis: Redis, group_ids: tuple[int, ...], ttl_seconds: int) -> None:
        self._redis = redis
        self._group_ids = group_ids
        self._ttl_seconds = ttl_seconds
        canonical = ",".join(str(group_id) for group_id in group_ids)
        self._groups_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    async def is_allowed(self, bot: Bot, user_id: int) -> bool:
        key = f"telegram:access:v1:{self._groups_hash}:{user_id}"
        try:
            cached = await self._redis.get(key)
        except RedisError:
            logger.warning("Telegram access cache unavailable", extra={"event": "telegram.access.cache_error", "fields": {}})
            return False
        if cached in {"allow", "deny"}:
            logger.info("Telegram access cache hit", extra={"event": "telegram.access.cache_hit", "fields": {"result": cached}})
            return cached == "allow"

        logger.info("Telegram access cache miss", extra={"event": "telegram.access.cache_miss", "fields": {}})

        try:
            allowed = False
            for group_id in self._group_ids:
                member = await bot.get_chat_member(group_id, user_id)
                if self._is_member(member):
                    allowed = True
                    break
        except Exception as exc:  # Telegram API exceptions always close access and are never cached.
            logger.warning("Telegram membership check failed", extra={"event": "telegram.access.api_error", "fields": {"error": type(exc).__name__}})
            return False
        try:
            await self._redis.set(key, "allow" if allowed else "deny", ex=self._ttl_seconds)
        except RedisError:
            logger.warning("Telegram access cache write failed", extra={"event": "telegram.access.cache_error", "fields": {}})
            return False
        logger.info("Telegram access checked", extra={"event": "telegram.access.result", "fields": {"result": "allow" if allowed else "deny"}})
        return allowed

    @staticmethod
    def _is_member(member: Any) -> bool:
        status = getattr(member, "status", "")
        status = getattr(status, "value", status)
        status = str(status)
        if status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER, "creator", "administrator", "member"}:
            return True
        return status in {ChatMemberStatus.RESTRICTED, "restricted"} and bool(getattr(member, "is_member", False))


class TelegramAccessMiddleware:
    def __init__(self, service: TelegramAccessService) -> None:
        self._service = service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        bot = data.get("bot")
        if user is None or bot is None or not await self._service.is_allowed(bot, user.id):
            return None
        return await handler(event, data)
