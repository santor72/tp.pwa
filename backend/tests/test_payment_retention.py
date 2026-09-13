import os
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.config import Settings
from app.models import PaymentJob, PaymentOutbox, PaymentJobArchive, PaymentTransaction, PaymentExecutionLease
from app.payment_events import PaymentStream
from app.payment_outbox import OutboxRelay
from app.payment_retention import PaymentStreamRetention, PaymentJobRetention
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values


@pytest.mark.asyncio
async def test_stream_retention_is_off_without_storage_access():
    assert await PaymentStreamRetention(None, Settings(_env_file=None), {'formation': None}).once() == {
        'scanned': 0, 'eligible': 0, 'deleted': 0,
    }
    assert await PaymentJobRetention(None, Settings(_env_file=None)).once() == 0


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_REDIS_URL') or not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG and Redis required')
async def test_quarantine_stream_cleanup_requires_old_durable_record_and_all_acks(event_db):
    from app.models import PaymentEventQuarantine
    repo, sessions, _ = event_db
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'quarantine-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_stream_retention_enabled=True)
    async def clean():
        return await PaymentStreamRetention(sessions, settings, {'formation': stream}).once()
    try:
        await stream.ensure_group()
        mid = await redis.xadd(stream.key, {'event': 'malformed'})
        await stream.read('test', block_ms=10)
        assert (await clean())['deleted'] == 0  # no durable quarantine
        await repo.quarantine(stream.key, mid, 'INVALID_ENVELOPE')
        await stream.ack(mid)
        assert (await clean())['deleted'] == 0  # recent record
        async with sessions.begin() as db:
            row = await db.scalar(select(PaymentEventQuarantine))
            row.created_at = datetime.now(UTC) - timedelta(days=31)
        await redis.xgroup_create(stream.key, 'other', id='0')
        assert (await clean())['deleted'] == 0  # unread by another group
        await redis.xreadgroup('other', 'test', {stream.key: '>'}, count=1)
        assert (await clean())['deleted'] == 0  # pending in another group
        await redis.xack(stream.key, 'other', mid)
        assert (await clean())['deleted'] == 1
        async with sessions() as db:
            assert await db.scalar(select(PaymentEventQuarantine)) is None
    finally:
        await redis.delete(stream.key)
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_REDIS_URL') or not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG and Redis required')
async def test_quarantine_record_survives_missing_body_pending_dryrun_and_redis_failure(event_db, monkeypatch):
    from app.models import PaymentEventQuarantine
    repo, sessions, _ = event_db
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'quarantine-pending-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_stream_retention_enabled=True)
    async def clean(**kwargs):
        return await PaymentStreamRetention(sessions, settings, {'formation': stream}).once(**kwargs)
    async def exists():
        async with sessions() as db:
            return await db.scalar(select(PaymentEventQuarantine)) is not None
    try:
        await stream.ensure_group()
        mid = await redis.xadd(stream.key, {'event': 'malformed'})
        await stream.read('test', block_ms=10)
        await repo.quarantine(stream.key, mid, 'INVALID_ENVELOPE')
        async with sessions.begin() as db:
            (await db.scalar(select(PaymentEventQuarantine))).created_at = datetime.now(UTC) - timedelta(days=31)
        await redis.xdel(stream.key, mid)  # Missing body is not proof of ACK.
        await clean()
        assert await exists()
        await stream.ack(mid)
        await clean(dry_run=True)
        assert await exists()
        async def unavailable(*args, **kwargs):
            raise ConnectionError('test Redis unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(redis, 'eval', unavailable)
            with pytest.raises(ConnectionError):
                await clean()
        assert await exists()
        await clean()
        assert not await exists()
    finally:
        await redis.delete(stream.key)
        await redis.aclose()


async def completed_history(event_db, unknown=False):
    repo, sessions, uid = event_db
    values = transaction_values(uid, now=datetime.now(UTC))
    values.update(status='canceled', current_step='canceled')
    tx, _ = await repo.create_payment(uuid4(), values)
    async with sessions() as session:
        job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
        outbox = await session.scalar(select(PaymentOutbox).where(PaymentOutbox.job_id == job.id))
    event = await repo.event_for(outbox.event_id)
    for index in range(2):
        if index:
            async with sessions.begin() as session:
                job = await repo.enqueue_in_session(session, tx.id, 'formation')
        claim = await repo.claim(job.id, 'retention-test', 30)
        if unknown and index == 0:
            await repo.begin_write(claim, 'unknown-retention', 'crm.contact.add')
        assert await repo.finish(claim)
        async with sessions.begin() as session:
            (await session.get(PaymentJob, job.id)).finished_at = datetime.now(UTC) - timedelta(days=31)
    return tx, event, job.id


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG required')
@pytest.mark.parametrize('protection', ['active_job', 'failed_job', 'unknown_write', 'paid_followups', 'live_lease'])
async def test_job_retention_protects_unfinished_operations(event_db, protection):
    repo, sessions, _ = event_db
    tx, event, latest = await completed_history(event_db, unknown=protection == 'unknown_write')
    async with sessions.begin() as session:
        if protection in {'active_job', 'failed_job'}:
            (await session.get(PaymentJob, latest)).state = 'ready' if protection == 'active_job' else 'failed'
        elif protection == 'paid_followups':
            (await session.get(PaymentTransaction, tx.id)).status = 'paid'
        elif protection == 'live_lease':
            (await session.get(PaymentExecutionLease, tx.id)).lease_until = datetime.now(UTC) + timedelta(seconds=30)
    assert await PaymentJobRetention(repo, Settings(_env_file=None, payment_job_retention_enabled=True)).once() == 0
    assert await repo.get_job(event.job_id) is not None


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG required')
async def test_job_archive_rolls_back_identity_and_delete_together(event_db, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.dml import Delete
    repo, sessions, _ = event_db
    _, event, _ = await completed_history(event_db)
    original = AsyncSession.execute
    async def fail_delete(self, statement, *args, **kwargs):
        if isinstance(statement, Delete) and statement.table.name == 'payment_jobs':
            raise RuntimeError('injected before removal')
        return await original(self, statement, *args, **kwargs)
    monkeypatch.setattr(AsyncSession, 'execute', fail_delete)
    with pytest.raises(RuntimeError):
        await PaymentJobRetention(repo, Settings(_env_file=None, payment_job_retention_enabled=True)).once()
    async with sessions() as session:
        assert await session.get(PaymentJob, event.job_id) is not None
        assert await session.get(PaymentOutbox, event.event_id) is not None
        assert await session.get(PaymentJobArchive, event.job_id) is None


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_REDIS_URL') or not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG and Redis required')
async def test_t26_archive_keeps_duplicate_identity_pending_ack_and_generation(event_db):
    from types import SimpleNamespace
    from app.payment_consumer import PaymentConsumer
    from app.payment_job_executor import PaymentJobExecutor
    repo, sessions, _ = event_db
    tx, event, latest = await completed_history(event_db)
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'archive-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_job_retention_enabled=True, payment_stream_retention_enabled=True)
    cleaner = PaymentJobRetention(repo, settings)
    try:
        await stream.ensure_group()
        await stream.publish(event)
        message_id, fields = (await stream.read('old-consumer', block_ms=10))[0]
        assert await cleaner.once(dry_run=True) == 1
        assert await repo.get_job(event.job_id) is not None
        assert sum(await asyncio.gather(cleaner.once(), cleaner.once())) == 1
        assert await repo.get_job(event.job_id) is None
        async with sessions() as session:
            assert await session.get(PaymentJobArchive, event.job_id) is not None
            assert await session.get(PaymentOutbox, event.event_id) is None
            assert (await session.get(PaymentJob, latest)).generation == 2
        assert await repo.valid_event(event)
        assert not await repo.valid_event(event.model_copy(update={'transaction_id': uuid4()}))
        retention = lambda: PaymentStreamRetention(sessions, settings, {'formation': stream})
        assert (await retention().once())['deleted'] == 0  # archived but still pending
        executor = PaymentJobExecutor(SimpleNamespace(), repo, settings, 'archive-replay')
        await PaymentConsumer(stream, repo, executor, 'archive-replay').handle(message_id, fields)
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
        assert (await retention().once())['deleted'] == 1
        assert await repo.backfill() == 0
        async with sessions.begin() as session:
            next_job = await repo.enqueue_in_session(session, tx.id, 'formation')
            assert next_job.generation == 3
    finally:
        await redis.delete(stream.key)
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_REDIS_URL') or not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PG and Redis required')
async def test_t26_retention_protects_pending_unfinished_and_unread_groups(event_db):
    repo, sessions, uid = event_db
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'retention-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_stream_retention_enabled=True)
    def cleaner(): return PaymentStreamRetention(sessions, settings, {'formation': stream})
    try:
        await stream.ensure_group()
        relay = OutboxRelay(repo, {'formation': stream})
        messages = []
        for index in range(3):
            tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
            assert await relay.once()
            message = (await stream.read('reader', block_ms=10))[0]
            messages.append(message)
            event = stream.decode(message[1])
            if index < 2:
                claim = await repo.claim(event.job_id, 'test', 30)
                assert await repo.finish(claim)
                async with sessions.begin() as session:
                    (await session.get(PaymentJob, event.job_id)).finished_at = datetime.now(UTC) - timedelta(days=31)
            if index != 1: await stream.ack(message[0])
        # A new/unexpected group has not read anything. Do not destroy its data.
        await redis.xgroup_create(stream.key, 'unread-test-group', id='0')
        result = await cleaner().once()
        assert result == {'scanned': 3, 'eligible': 2, 'deleted': 0}
        await redis.xgroup_destroy(stream.key, 'unread-test-group')
        assert (await cleaner().once(dry_run=True))['deleted'] == 0
        assert await redis.xlen(stream.key) == 3
        result = await cleaner().once()
        assert result['deleted'] == 1
        remaining = await redis.xrange(stream.key)
        assert [mid for mid, _ in remaining] == [messages[1][0], messages[2][0]]
        assert (await redis.xpending(stream.key, stream.group))['pending'] == 1
        # ACK after recovery permits old completed message cleanup, not unfinished work.
        await stream.ack(messages[1][0])
        assert (await cleaner().once())['deleted'] == 1
        assert (await redis.xrange(stream.key))[0][0] == messages[2][0]
        for message in messages:
            assert await repo.valid_event(stream.decode(message[1]))  # durable identity survives cleanup
        unfinished = stream.decode(messages[2][1])
        claim = await repo.claim(unfinished.job_id, 'recovery-after-cleanup', 30)
        assert claim is not None
        assert await repo.finish(claim)
    finally:
        await redis.delete(stream.key)
        await redis.aclose()
