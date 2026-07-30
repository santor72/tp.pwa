import fakeredis.aioredis
import pytest

from app.cache_store import CacheStore


@pytest.mark.asyncio
async def test_cache_store_returns_json_value() -> None:
    cache = CacheStore(fakeredis.aioredis.FakeRedis(decode_responses=True))

    await cache.set_json("domofon:addresses:v1", [{"locid": 1, "loctext": "Тест"}], 60)

    assert await cache.get_json("domofon:addresses:v1") == [{"locid": 1, "loctext": "Тест"}]
