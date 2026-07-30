import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class CacheStore:
    """Нефатальный Redis-кэш: ошибки кэша не блокируют read-операции."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get_json(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw is not None else None
        except (RedisError, json.JSONDecodeError) as exc:
            logger.warning("Redis-кэш недоступен", extra={"event": "cache.read.failed", "fields": {"error": type(exc).__name__}})
            return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            await self._redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("Redis-кэш недоступен", extra={"event": "cache.write.failed", "fields": {"error": type(exc).__name__}})
