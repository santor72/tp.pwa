"""Redis delivery contract; isolated DB and Stream prefixes per test."""
import os
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("PAYMENT_EVENTS_TEST_REDIS_URL"), reason="isolated Redis required")
async def test_graceful_stop_leaves_reclaimed_jobs_for_next_consumer():
    from app.payment_consumer import PaymentConsumer
    from app.payment_events import PaymentEvent, PaymentStream
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    stop = asyncio.Event()
    completed = []
    async def execute(job_id, **kwargs):
        completed.append(job_id)
        stop.set()
        return True
    repo = SimpleNamespace(valid_event=AsyncMock(return_value=True))
    executor = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    events = [PaymentEvent(event_id=uuid4(), job_id=uuid4(), transaction_id=uuid4(),
        event_type='payment.formation_requested', generation=1) for _ in range(3)]
    try:
        await stream.ensure_group()
        for event in events:
            await stream.publish(event)
            await stream.read('previous', block_ms=10)
        consumer = PaymentConsumer(stream, repo, executor, 'stopping', reclaim_idle_ms=0)
        await consumer.run(stop)
        assert completed == [events[0].job_id]
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 2
        _, pending = await stream.reclaim('replacement', idle_ms=0)
        assert [stream.decode(fields).job_id for _, fields in pending] == [e.job_id for e in events[1:]]
        replacement_executor = SimpleNamespace(execute=AsyncMock(return_value=True))
        replacement = PaymentConsumer(stream, repo, replacement_executor, 'replacement')
        for message_id, fields in pending:
            await replacement.handle(message_id, fields)
        assert replacement_executor.execute.await_count == 2
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
    finally:
        await redis.delete(stream.key)
        await redis.aclose()


def test_t12_envelope_rejects_unknown_version_and_extra_fields():
    from app.payment_events import PaymentEvent
    valid = dict(event_id=str(uuid4()), job_id=str(uuid4()), transaction_id=str(uuid4()),
                 event_type="payment.formation_requested", schema_version=1, generation=1)
    assert PaymentEvent.model_validate(valid).generation == 1
    for bad in ({**valid, "schema_version": 99}, {**valid, "phone": "private"}, {**valid, "event_type": "unknown"}):
        with pytest.raises(ValidationError): PaymentEvent.model_validate(bad)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("PAYMENT_EVENTS_TEST_REDIS_URL"), reason="isolated Redis required")
async def test_t07_t14_redis_distribution_pending_reclaim_and_ack():
    from app.payment_events import PaymentEvent, PaymentStream
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    prefix = 'events-test-' + uuid4().hex
    stream = PaymentStream(redis, prefix, 'formation')
    event = PaymentEvent(event_id=uuid4(), job_id=uuid4(), transaction_id=uuid4(),
                         event_type='payment.formation_requested', schema_version=1, generation=1)
    try:
        await stream.ensure_group()
        await stream.publish(event)
        first = await stream.read('first', block_ms=10)
        assert len(first) == 1
        assert await stream.read('second', block_ms=10) == []
        cursor, recovered = await stream.reclaim('second', idle_ms=0)
        assert recovered[0][0] == first[0][0]
        assert stream.decode(recovered[0][1]) == event
        await stream.ack(recovered[0][0])
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
        # A missing group is reconstructed and persisted messages become visible again.
        await redis.xgroup_destroy(stream.key, stream.group)
        await stream.ensure_group()
        assert len(await stream.read('third', block_ms=10)) == 1
    finally:
        await redis.delete(stream.key)  # Only this test's UUID-namespaced Stream.
        await redis.aclose()
