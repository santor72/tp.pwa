"""T14: lose Stream/group/PEL together, recover from DB without repeating work."""
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import Settings
from app.models import PaymentJob, PaymentOutbox
from app.payment_consumer import PaymentConsumer
from app.payment_events import PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.payment_outbox import OutboxRelay
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
async def test_whole_stream_loss_recovers_only_unfinished_job(event_db):
    queue, sessions, uid = event_db
    payments = PaymentRepository(sessions)
    for _ in range(2):
        await queue.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    service = SimpleNamespace(process=AsyncMock(side_effect=payments.get))
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments, payment_service=service),
        queue, Settings(_env_file=None, payment_telemetry_enabled=False), 'loss-test')
    consumer = PaymentConsumer(stream, queue, executor, 'loss-test')
    relay = OutboxRelay(queue, {'formation': stream})
    try:
        await stream.ensure_group()
        assert await relay.once() and await relay.once()
        first = (await stream.read('lost-consumer', block_ms=10))[0]
        second = (await stream.read('lost-consumer', block_ms=10))[0]
        first_event, second_event = stream.decode(first[1]), stream.decode(second[1])
        assert await executor.execute(first_event.job_id)  # DB commit, no Redis ACK
        assert service.process.await_count == 1
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 2
        await redis.delete(stream.key)  # UUID key owned only by this test, not FLUSHDB
        assert not await redis.exists(stream.key)
        await stream.ensure_group()
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
        # Zero staleness avoids advancing wall time; production uses configured interval.
        assert await queue.recover_publications(stale_seconds=0) == 1
        assert await relay.once()
        assert not await relay.once()
        recovered = (await stream.read('replacement', block_ms=10))[0]
        assert stream.decode(recovered[1]) == second_event
        await consumer.handle(*recovered)
        assert service.process.await_count == 2
        # Old event may reappear from an at-least-once producer after restoration.
        await stream.publish(first_event)
        await consumer.handle(*(await stream.read('replacement', block_ms=10))[0])
        assert service.process.await_count == 2
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(PaymentJob)) == 2
            assert await db.scalar(select(func.count()).select_from(PaymentOutbox)) == 2
            assert set(await db.scalars(select(PaymentJob.state))) == {'completed'}
    finally:
        await redis.delete(stream.key)
        await redis.aclose()
