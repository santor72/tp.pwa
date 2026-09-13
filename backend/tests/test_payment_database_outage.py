"""T29: break real PostgreSQL TCP connections, never the shared DB container."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentExecutionLease, PaymentJob
from app.payment_consumer import PaymentConsumer
from app.payment_event_repository import PaymentEventRepository
from app.payment_events import PaymentEvent, PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


class DatabaseGate:
    """Local forwarding socket; cuts only connections made by this test."""
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.enabled = True
        self.writers = set()
        self.tasks = set()

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        pumps = []
        self.writers.add(writer)
        try:
            if not self.enabled:
                return
            remote, upstream = await asyncio.open_connection(self.host, self.port)
            self.writers.add(upstream)
            async def copy(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
            pumps = [asyncio.create_task(copy(reader, upstream)),
                     asyncio.create_task(copy(remote, writer))]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for connection in (writer, upstream):
                if connection:
                    connection.close()
                    self.writers.discard(connection)
            self.tasks.discard(task)

    async def cut(self):
        self.enabled = False
        for writer in list(self.writers):
            writer.close()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_at', ['validation', 'before_write', 'before_commit'])
async def test_database_outage_keeps_pending_and_recovers_without_duplicate(event_db, failure_at):
    original, sessions, uid = event_db
    tx, _ = await original.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as db:
        job = await db.scalar(select(PaymentJob))
        schema = await db.scalar(text('SELECT current_schema()'))
    url = sessions.kw['bind'].url
    gate = DatabaseGate(url.host, url.port or 5432)
    server = await asyncio.start_server(gate.accept, '127.0.0.1', 0)
    engine = create_async_engine(url.set(host='127.0.0.1', port=server.sockets[0].getsockname()[1]),
        connect_args={'server_settings': {'search_path': schema}, 'timeout': 2}, pool_pre_ping=True)
    gated_sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo, payments = PaymentEventRepository(gated_sessions), PaymentRepository(gated_sessions)
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_telemetry_enabled=False,
        bx24_webhook='https://fake.invalid/rest/1/test/')
    calls = []
    def remote(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'result': 123})
    inject = True
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as http:
            bitrix = Bitrix24Client(settings, http)
            class Service:
                async def process(self, tid):
                    if inject and failure_at == 'before_write':
                        await gate.cut()
                    await bitrix.call('crm.contact.add', {'fields': {'ORIGIN_ID': 'test-outage'}})
                    result = await payments.get(tid)
                    if inject and failure_at == 'before_commit':
                        await gate.cut()
                    return result
            executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments,
                payment_service=Service(), bitrix=bitrix), repo, settings, 'outage-test')
            consumer = PaymentConsumer(stream, repo, executor, 'outage-test')
            await stream.ensure_group()
            async with sessions() as db:
                from app.models import PaymentOutbox
                outbox = await db.scalar(select(PaymentOutbox))
            event = PaymentEvent(event_id=outbox.event_id, job_id=job.id,
                transaction_id=tx.id, event_type='payment.formation_requested', generation=job.generation)
            await stream.publish(event)
            message_id, fields = (await stream.read('outage-test', block_ms=10))[0]
            if failure_at == 'validation':
                await gate.cut()
            with pytest.raises(Exception) as failure:
                await asyncio.wait_for(consumer.handle(message_id, fields), 5)
            assert not isinstance(failure.value, TimeoutError), 'consumer must fail promptly, not hang'
            assert len(calls) == (1 if failure_at == 'before_commit' else 0)
            assert (await redis.xpending(stream.key, stream.group))['pending'] == 1
            assert (await original.get_job(job.id)).state not in {'completed', 'superseded', 'failed'}
            # Model expiry rather than waiting the production lease duration.
            async with sessions.begin() as db:
                lease = await db.get(PaymentExecutionLease, tx.id)
                if lease:
                    lease.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            gate.enabled = True
            inject = False
            await engine.dispose()
            await consumer.handle(message_id, fields)
            assert (await original.get_job(job.id)).state == 'completed'
            assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
            assert len(calls) == 1
    finally:
        await engine.dispose()
        await gate.cut()
        server.close()
        await server.wait_closed()
        await redis.delete(stream.key)
        await redis.aclose()
