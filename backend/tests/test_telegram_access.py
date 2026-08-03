from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.telegram_access import TelegramAccessService


class FakeBot:
    def __init__(self, members: dict[tuple[int, int], object]) -> None:
        self.members = members
        self.calls: list[tuple[int, int]] = []

    async def get_chat_member(self, chat_id: int, user_id: int) -> object:
        self.calls.append((chat_id, user_id))
        value = self.members[(chat_id, user_id)]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_access_checks_groups_in_order_and_caches_allow() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bot = FakeBot({
        (-1001, 5): SimpleNamespace(status="left", is_member=False),
        (-1002, 5): SimpleNamespace(status="member", is_member=True),
    })
    service = TelegramAccessService(redis, (-1001, -1002), 60)

    assert await service.is_allowed(bot, 5) is True
    assert bot.calls == [(-1001, 5), (-1002, 5)]
    assert await service.is_allowed(bot, 5) is True
    assert bot.calls == [(-1001, 5), (-1002, 5)]


@pytest.mark.asyncio
async def test_access_fails_closed_and_does_not_cache_telegram_error() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bot = FakeBot({(-1001, 5): RuntimeError("network")})
    service = TelegramAccessService(redis, (-1001,), 60)

    assert await service.is_allowed(bot, 5) is False
    assert await redis.keys("telegram:access:*") == []


@pytest.mark.asyncio
async def test_restricted_member_is_allowed_but_left_is_not() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    service = TelegramAccessService(redis, (-1001,), 60)
    assert await service.is_allowed(FakeBot({(-1001, 5): SimpleNamespace(status="restricted", is_member=True)}), 5)
    assert not await service.is_allowed(FakeBot({(-1001, 6): SimpleNamespace(status="restricted", is_member=False)}), 6)
