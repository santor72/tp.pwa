"""T12: full consumer rejection, durable quarantine, ACK ordering and continuation."""
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.models import PaymentEventQuarantine, PaymentJob, PaymentOutbox
from app.payment_consumer import PaymentConsumer
from app.payment_events import PaymentEvent, PaymentStream
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['json', 'version', 'type', 'transaction', 'extra'])
async def test_invalid_delivery_requires_durable_quarantine_then_continues(event_db, monkeypatch, corruption):
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as db:
        job = await db.scalar(select(PaymentJob))
        outbox = await db.scalar(select(PaymentOutbox))
    event = PaymentEvent(event_id=outbox.event_id, job_id=job.id, transaction_id=tx.id,
        event_type='payment.formation_requested', generation=job.generation)
    payload = event.model_dump(mode='json')
    if corruption == 'version': payload['schema_version'] = 99
    if corruption == 'type': payload['event_type'] = 'unknown'
    if corruption == 'transaction': payload['transaction_id'] = str(uuid4())
    if corruption == 'extra': payload['phone'] = 'PRIVATE-TEST-SENTINEL'
    raw = '{PRIVATE-TEST-SENTINEL' if corruption == 'json' else json.dumps(payload)
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    executed = []
    async def execute(job_id, **kwargs):
        executed.append(job_id)
        claim = await repo.claim(job_id, 'test-consumer', 30)
        assert claim is not None
        return await repo.finish(claim)
    consumer = PaymentConsumer(stream, repo, SimpleNamespace(execute=execute), 'test-consumer')
    original_quarantine = repo.quarantine
    async def unavailable(*args):
        raise RuntimeError('test quarantine storage unavailable')
    try:
        await stream.ensure_group()
        bad_id = await redis.xadd(stream.key, {'event': raw})
        bad_message = (await stream.read('test-consumer', block_ms=10))[0]
        monkeypatch.setattr(repo, 'quarantine', unavailable)
        with pytest.raises(RuntimeError, match='quarantine storage unavailable'):
            await consumer.handle(*bad_message)
        assert not executed
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 1
        async with sessions() as db:
            assert not list(await db.scalars(select(PaymentEventQuarantine)))
        monkeypatch.setattr(repo, 'quarantine', original_quarantine)
        await consumer.handle(*bad_message)
        await consumer.handle(*bad_message)  # idempotent quarantine on redelivery
        assert not executed
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
        async with sessions() as db:
            rows = list(await db.scalars(select(PaymentEventQuarantine)))
            assert len(rows) == 1
            assert rows[0].message_id == bad_id
            assert rows[0].reason == ('EVENT_MISMATCH' if corruption == 'transaction' else 'INVALID_ENVELOPE')
            assert 'PRIVATE-TEST-SENTINEL' not in str(rows[0].__dict__)
        # The next legitimate event is not poisoned by the rejected predecessor.
        await stream.publish(event)
        await consumer.handle(*(await stream.read('test-consumer', block_ms=10))[0])
        assert executed == [job.id]
        assert (await repo.get_job(job.id)).state == 'completed'
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
    finally:
        await redis.delete(stream.key)
        await redis.aclose()
