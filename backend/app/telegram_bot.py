import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Message
from redis.asyncio import Redis

from app.actors import Actor
from app.config import Settings, get_settings
from app.errors import MessengerLinkError
from app.logging import configure_logging
from app.services import ApplicationServices, create_application_services
from app.telegram_access import TelegramAccessMiddleware, TelegramAccessService

logger = logging.getLogger(__name__)


class TelegramUser(Protocol):
    id: int
    username: str | None
    full_name: str


async def handle_start(
    services: ApplicationServices,
    user: TelegramUser,
    payload: str,
    answer: Callable[[str], Awaitable[object]],
) -> None:
    payload = payload.strip()
    if payload:
        if len(payload) > 128 or not payload.replace("-", "").replace("_", "").isalnum():
            await answer("Ссылка для привязки недействительна.")
            return
        try:
            identity = await services.messenger_links.consume("telegram", payload, str(user.id), user.username, user.full_name)
        except MessengerLinkError:
            await answer("Не удалось привязать Telegram. Создайте новую ссылку в PWA.")
            return
        await answer("Telegram подключён. Используйте /start, чтобы увидеть заявки на сегодня.")
        logger.info("Telegram linked", extra={"event": "telegram.link.succeeded", "fields": {"user_id": str(identity.user_id)}})
        return

    identity = await services.messenger_links.active_for_provider_user("telegram", str(user.id))
    if identity is None:
        await answer("Сначала подключите Telegram в настройках PWA ТехПортала.")
        return
    try:
        actor = Actor(
            user_id=identity.user_id,
            techportal_user_id=identity.techportal_user_id,
            channel="messenger",
            provider="telegram",
            external_identity_id=identity.id,
            permissions=identity.permissions,
            role=identity.role,
        )
        tickets = await services.ticket_service.list_for_actor(actor, "today")
    except Exception:
        logger.warning("Telegram tickets unavailable", extra={"event": "telegram.tickets.failed", "fields": {"user_id": str(identity.user_id)}})
        await answer("Не удалось получить заявки. Попробуйте позже.")
        return
    await answer(f"Заявок на сегодня: {len(tickets)}")


def build_dispatcher(services: ApplicationServices, storage: RedisStorage, access: TelegramAccessService) -> Dispatcher:
    dispatcher = Dispatcher(storage=storage)
    router = Router(name="telegram-main")
    router.message.middleware(TelegramAccessMiddleware(access))
    router.callback_query.middleware(TelegramAccessMiddleware(access))

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await handle_start(services, message.from_user, command.args or "", message.answer)

    dispatcher.include_router(router)
    return dispatcher


def validate_bot_settings(settings: Settings) -> tuple[int, ...]:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не настроен")
    if not settings.https_proxy:
        raise RuntimeError("HTTPS_PROXY обязателен для Telegram long polling")
    group_ids = settings.telegram_access_group_ids
    if not group_ids:
        raise RuntimeError("TG_ACCESS_GROUPS не должен быть пустым")
    return group_ids


def create_bot(settings: Settings) -> Bot:
    """Create the only Telegram HTTP client; proxy is deliberately explicit."""
    return Bot(settings.telegram_bot_token, session=AiohttpSession(proxy=settings.https_proxy))


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    group_ids = validate_bot_settings(settings)
    redis = Redis.from_url(settings.bot_redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis, key_builder=DefaultKeyBuilder(prefix="telegram_fsm", with_bot_id=True))
    services = create_application_services(settings, redis)
    bot = create_bot(settings)
    access = TelegramAccessService(redis, group_ids, settings.tg_access_cache_ttl_seconds)
    dispatcher = build_dispatcher(services, storage, access)
    heartbeat: asyncio.Task[None] | None = None
    try:
        await redis.ping()
        async with services.engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")
        heartbeat = asyncio.create_task(_publish_readiness(redis))
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        with contextlib.suppress(Exception):
            await redis.delete("telegram:bot:ready")
        await dispatcher.storage.close()
        await bot.session.close()
        await services.close()
        await redis.aclose()


async def _publish_readiness(redis: Redis) -> None:
    while True:
        await redis.set("telegram:bot:ready", "ok", ex=60)
        await asyncio.sleep(20)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
