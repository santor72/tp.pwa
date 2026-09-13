"""T03/T04: concurrent relays, released DB locks during XADD, lost publish commit."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.models import PaymentJob, PaymentOutbox
from app.payment_event_repository import PaymentEventRepository
from app.payment_events import PaymentStream
from app.payment_outbox import OutboxRelay
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
async def test_two_relays_drain_backlog_without_network_transaction_or_lost_event(event_db, monkeypatch):
    repo, sessions, uid = event_db
    for _ in range(16):
        await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    blocked = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    counts = [0, 0]
    repositories = [repo, PaymentEventRepository(sessions)]
    class PausingStream:
        def __init__(self, index): self.index = index
        async def publish(self, event):
            counts[self.index] += 1
            if counts[self.index] == 1:
                blocked[self.index].set()
                await release.wait()
            return await stream.publish(event)
    original = repo.published
    fail_once = True
    async def lost_commit(*args):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError('test DB publication marker lost after successful XADD')
        return await original(*args)
    monkeypatch.setattr(repo, 'published', lost_commit)
    original_failed = repo.publish_failed
    async def hold_retry(event_id, token, delay):
        # Advance this deadline explicitly below; avoid timing-dependent retries
        # while the other relay drains the backlog on a busy test machine.
        return await original_failed(event_id, token, 60)
    monkeypatch.setattr(repo, 'publish_failed', hold_retry)
    relays = [OutboxRelay(repositories[i], {'formation': PausingStream(i)}) for i in range(2)]
    async def drain(relay):
        while await relay.once(): pass
    tasks = [asyncio.create_task(drain(relay)) for relay in relays]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in blocked)), 5)
        # Both publishers are inside their network step. A third session can lock
        # every outbox row NOWAIT: publishers retained no DB transaction/row lock.
        async with sessions.begin() as db:
            rows = list(await db.scalars(select(PaymentOutbox).with_for_update(nowait=True)))
            assert len(rows) == 16
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        async with sessions.begin() as db:
            pending = list(await db.scalars(select(PaymentOutbox).where(PaymentOutbox.last_published_at.is_(None))))
            assert len(pending) == 1
            pending[0].next_publish_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await relays[0].once()
        messages = [stream.decode(fields) for _, fields in await redis.xrange(stream.key)]
        assert len(messages) == 17 and len({event.event_id for event in messages}) == 16
        assert all(counts)
        async with sessions() as db:
            rows = list(await db.scalars(select(PaymentOutbox)))
            assert all(row.last_published_at and row.completed_at is None for row in rows)
            assert {row.event_id for row in rows} == {event.event_id for event in messages}
            assert set(await db.scalars(select(PaymentJob.state))) == {'ready'}
    finally:
        release.set()
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redis.delete(stream.key)
        await redis.aclose()
