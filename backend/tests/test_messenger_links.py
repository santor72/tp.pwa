from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import pytest
import pytest_asyncio

from app.errors import MessengerLinkError, MessengerLinkTokenError
from app.messenger_links import MessengerLinkService
from app.models import Base
from app.schemas import UserProfile


@pytest_asyncio.fixture
async def links() -> MessengerLinkService:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = MessengerLinkService(async_sessionmaker(engine, expire_on_commit=False), 60)
    yield service
    await engine.dispose()


@pytest.mark.asyncio
async def test_link_token_is_one_time_and_only_hash_is_stored(links: MessengerLinkService) -> None:
    user = await links.upsert_user(UserProfile(id=7, email="tech@example.test"))
    created = await links.create(user.id, "telegram")

    identity = await links.consume("telegram", created.token, "42", "tech", "Техник")
    assert identity.techportal_user_id == "7"
    assert identity.provider_user_id == "42"
    assert await links.active_for_provider_user("telegram", "42") == identity

    with pytest.raises(MessengerLinkTokenError):
        await links.consume("telegram", created.token, "43", None, None)


@pytest.mark.asyncio
async def test_new_link_invalidates_previous_and_revoke_blocks_next_update(links: MessengerLinkService) -> None:
    user = await links.upsert_user(UserProfile(id=7, email="tech@example.test"))
    first = await links.create(user.id, "telegram")
    second = await links.create(user.id, "telegram")
    with pytest.raises(MessengerLinkTokenError):
        await links.consume("telegram", first.token, "42", None, None)

    await links.consume("telegram", second.token, "42", None, None)
    await links.revoke(user.id, "telegram")
    assert await links.active_for_provider_user("telegram", "42") is None
    with pytest.raises(MessengerLinkError, match="не найдена"):
        await links.revoke(user.id, "telegram")


@pytest.mark.asyncio
async def test_cannot_consume_for_wrong_provider_or_reuse_telegram_identity(links: MessengerLinkService) -> None:
    first_user = await links.upsert_user(UserProfile(id=7, email="first@example.test"))
    second_user = await links.upsert_user(UserProfile(id=8, email="second@example.test"))
    first_link = await links.create(first_user.id, "telegram")
    with pytest.raises(MessengerLinkTokenError):
        await links.consume("other", first_link.token, "42", None, None)
    await links.consume("telegram", first_link.token, "42", None, None)

    second_link = await links.create(second_user.id, "telegram")
    with pytest.raises(MessengerLinkError, match="другим пользователем"):
        await links.consume("telegram", second_link.token, "42", None, None)
