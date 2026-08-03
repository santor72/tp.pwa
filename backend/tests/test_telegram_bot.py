import pytest
from types import SimpleNamespace

from app.config import Settings
import app.telegram_bot as telegram_bot
from app.telegram_bot import create_bot, validate_bot_settings


def test_bot_requires_proxy_for_long_polling() -> None:
    settings = Settings(telegram_bot_token="123:abc", tg_access_groups="-1001")
    with pytest.raises(RuntimeError, match="HTTPS_PROXY"):
        validate_bot_settings(settings)


def test_bot_requires_access_groups() -> None:
    settings = Settings(telegram_bot_token="123:abc", https_proxy="http://proxy.test:3128")
    with pytest.raises(RuntimeError, match="TG_ACCESS_GROUPS"):
        validate_bot_settings(settings)


def test_bot_accepts_valid_configuration() -> None:
    settings = Settings(
        telegram_bot_token="123:abc",
        https_proxy="http://proxy.test:3128",
        tg_access_groups="-1001,-1002",
    )
    assert validate_bot_settings(settings) == (-1001, -1002)


@pytest.mark.asyncio
async def test_bot_session_uses_explicit_https_proxy() -> None:
    settings = Settings(
        telegram_bot_token="123:abc",
        https_proxy="http://proxy.test:3128",
        tg_access_groups="-1001",
    )
    bot = create_bot(settings)
    try:
        assert bot.session._connector_init["host"] == "proxy.test"
        assert bot.session._connector_init["port"] == 3128
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_run_bot_starts_and_closes_dependencies_without_telegram_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeRedis:
        async def ping(self) -> None: calls.append("redis.ping")
        async def set(self, *args: object, **kwargs: object) -> None: calls.append("redis.set")
        async def delete(self, *args: object) -> None: calls.append("redis.delete")
        async def aclose(self) -> None: calls.append("redis.close")

    class FakeConnection:
        async def __aenter__(self) -> "FakeConnection": return self
        async def __aexit__(self, *args: object) -> None: return None
        async def exec_driver_sql(self, _: str) -> None: calls.append("db.check")

    class FakeEngine:
        def connect(self) -> FakeConnection: return FakeConnection()

    class FakeServices:
        engine = FakeEngine()
        async def close(self) -> None: calls.append("services.close")

    class FakeStorage:
        async def close(self) -> None: calls.append("storage.close")

    class FakeDispatcher:
        storage = FakeStorage()
        def resolve_used_update_types(self) -> list[str]: return ["message"]
        async def start_polling(self, bot: object, *, allowed_updates: list[str]) -> None:
            assert allowed_updates == ["message"]
            calls.append("polling")

    class FakeSession:
        async def close(self) -> None: calls.append("bot.close")

    class FakeBot:
        session = FakeSession()
        async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
            assert drop_pending_updates is False
            calls.append("webhook.delete")

    fake_redis = FakeRedis()
    monkeypatch.setattr(telegram_bot.Redis, "from_url", lambda *args, **kwargs: fake_redis)
    monkeypatch.setattr(telegram_bot, "RedisStorage", lambda **kwargs: object())
    monkeypatch.setattr(telegram_bot, "create_application_services", lambda *args: FakeServices())
    monkeypatch.setattr(telegram_bot, "create_bot", lambda settings: FakeBot())
    monkeypatch.setattr(telegram_bot, "build_dispatcher", lambda *args: FakeDispatcher())

    await telegram_bot.run_bot(Settings(telegram_bot_token="123:abc", https_proxy="http://proxy.test:3128", tg_access_groups="-1001"))

    assert {"redis.ping", "db.check", "webhook.delete", "polling", "storage.close", "bot.close", "services.close", "redis.close"} <= set(calls)
