"""T15: Redis consumer and direct recovery race for exactly one payment lease."""
import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.config import Settings
from app.models import PaymentJob, PaymentOutbox
from app.payment_consumer import PaymentConsumer
from app.payment_events import PaymentEvent, PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.payment_recovery import PaymentRecovery
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
async def test_consumer_and_fallback_race_share_one_lease(event_db):
    queue, sessions, uid = event_db
    tx, _ = await queue.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as db:
        job = await db.scalar(select(PaymentJob))
        outbox = await db.scalar(select(PaymentOutbox))
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    entered, release = asyncio.Event(), asyncio.Event()
    payments = PaymentRepository(sessions)
    calls = []
    class Service:
        async def process(self, transaction_id):
            calls.append(transaction_id)
            entered.set()
            await release.wait()
            return await payments.get(transaction_id)
    services = SimpleNamespace(payment_repository=payments, payment_service=Service())
    settings = Settings(_env_file=None, payment_telemetry_enabled=False)
    consumer_executor = PaymentJobExecutor(services, queue, settings, 'redis-consumer')
    fallback_executor = PaymentJobExecutor(services, queue, settings, 'postgres-fallback')
    consumer = PaymentConsumer(stream, queue, consumer_executor, 'redis-consumer')
    recovery = PaymentRecovery(queue, fallback_executor, 'formation', interval=1)
    event = PaymentEvent(event_id=outbox.event_id, job_id=job.id, transaction_id=tx.id,
        event_type='payment.formation_requested', generation=job.generation)
    try:
        await stream.ensure_group()
        await stream.publish(event)
        delivery = (await stream.read('redis-consumer', block_ms=10))[0]
        consumer_task = asyncio.create_task(consumer.handle(*delivery))
        await asyncio.wait_for(entered.wait(), 2)
        # Once the consumer commits running+lease, fallback's due-job query does
        # not even offer it for claim; this avoids needless active contention.
        assert await recovery.once() == 0
        assert calls == [tx.id]
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 1
        release.set()
        await asyncio.wait_for(consumer_task, 3)
        assert calls == [tx.id]
        assert (await queue.get_job(job.id)).state == 'completed'
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
    finally:
        release.set()
        if 'consumer_task' in locals() and not consumer_task.done(): consumer_task.cancel()
        if 'consumer_task' in locals(): await asyncio.gather(consumer_task, return_exceptions=True)
        await redis.delete(stream.key)
        await redis.aclose()
