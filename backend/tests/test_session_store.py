from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from app.config import Settings
from app.schemas import UserProfile
from app.session_store import SessionStore


@pytest.mark.asyncio
async def test_session_is_created_and_read() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = Settings(session_absolute_ttl_seconds=60, session_idle_ttl_seconds=30)
    store = SessionStore(redis, settings)
    user = UserProfile(
        id=7,
        email="tech@example.test",
        first_name="Техник",
        status="user",
        user_permissions={
            "map": {"lines": "w", "clients": True},
            "tickets": {"all": True, "execution": True},
        },
    )

    session_id, created = await store.create(user)
    loaded = await store.get(session_id)

    assert loaded.user == user
    assert loaded.csrf_token == created.csrf_token
    assert loaded.absolute_expires_at > datetime.now(UTC)
    assert await redis.ttl(f"session:{session_id}") <= 30


@pytest.mark.asyncio
async def test_session_is_deleted() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis, Settings(session_absolute_ttl_seconds=60, session_idle_ttl_seconds=30))
    session_id, _ = await store.create(UserProfile(id=7, email="tech@example.test"))

    await store.delete(session_id)

    assert await redis.get(f"session:{session_id}") is None
