from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.messenger_links import MessengerLinkService
from app.models import Base
from app.schemas import UserProfile
from app.telegram_bot import handle_start


class FakeTickets:
    def __init__(self) -> None:
        self.actor = None

    async def list_for_actor(self, actor: object, day: str) -> list[object]:
        self.actor = actor
        assert day == "today"
        return [object(), object(), object()]


@pytest_asyncio.fixture
async def links() -> MessengerLinkService:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = MessengerLinkService(async_sessionmaker(engine, expire_on_commit=False), 60)
    yield service
    await engine.dispose()


@pytest.mark.asyncio
async def test_link_then_start_returns_today_count(links: MessengerLinkService) -> None:
    user = await links.upsert_user(UserProfile(id=15, email="tech@example.test"))
    link = await links.create(user.id, "telegram")
    tickets = FakeTickets()
    services = SimpleNamespace(messenger_links=links, ticket_service=tickets)
    telegram_user = SimpleNamespace(id=777, username="tech", full_name="Техник")
    replies: list[str] = []

    async def answer(text: str) -> None:
        replies.append(text)

    await handle_start(services, telegram_user, link.token, answer)
    await handle_start(services, telegram_user, "", answer)

    assert replies == [
        "Telegram подключён. Используйте /start, чтобы увидеть заявки на сегодня.",
        "Заявок на сегодня: 3",
    ]
    assert tickets.actor is not None
    assert tickets.actor.techportal_user_id == "15"
    assert tickets.actor.channel == "messenger"
