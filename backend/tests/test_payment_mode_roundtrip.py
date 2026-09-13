"""T28: an existing payment survives events -> compatible legacy -> events."""
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentJob, PaymentOutbox
from app.payment_client_resolver import PaymentClientResolver
from app.payment_consumer import PaymentConsumer
from app.payment_event_repository import PaymentEventRepository
from app.payment_events import PaymentEvent, PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.payment_recovery import PaymentRecovery
from app.payment_runtime_registry import PaymentRuntimeRegistry
from app.payment_status import PaymentStatusHandler
from app.payments import PaymentService
from app.repositories import PaymentRepository
from runtime_fake_bitrix import create_app
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
async def test_active_payment_mode_roundtrip_keeps_crm_progress(event_db):
    _, sessions, uid = event_db
    payments = PaymentRepository(sessions)
    values = transaction_values(uid, now=datetime.now(UTC))
    values.update(email=None)
    tx, _ = await payments.create_or_get(idempotency_key=uuid4(), values=values)
    registry = PaymentRuntimeRegistry(sessions, 'legacy')
    await registry.set_mode('events')
    queue = PaymentEventRepository(sessions, expected_mode='events')
    assert await queue.backfill() == 1
    assert await queue.backfill() == 0
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    settings = Settings(_env_file=None, payment_processing_mode='events',
        bx24_webhook='http://fake/rest/1/test', bx24_payment_link_field='ufCrmTestLink',
        bx24_payment_send_trigger='stageId=TEST:SEND')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='http://fake') as http:
            bitrix = Bitrix24Client(settings, http)
            service = PaymentService(settings, payments, SimpleNamespace(),
                PaymentClientResolver(settings, bitrix), bitrix)
            services = SimpleNamespace(payment_repository=payments, payment_service=service,
                payment_status=PaymentStatusHandler(settings, payments, bitrix), bitrix=bitrix)
            executor = PaymentJobExecutor(services, queue, settings, 'events-formation')
            consumer = PaymentConsumer(stream, queue, executor, 'events-formation')
            async with sessions() as db:
                job = await db.scalar(select(PaymentJob))
                outbox = await db.scalar(select(PaymentOutbox))
            event = PaymentEvent(event_id=outbox.event_id, job_id=job.id,
                transaction_id=tx.id, event_type='payment.formation_requested', generation=job.generation)
            await stream.ensure_group()
            await stream.publish(event)
            message = (await stream.read('events-formation', block_ms=10))[0]
            await consumer.handle(*message)
            formed = await payments.get(tx.id)
            assert formed.status == 'send_queued' and formed.payment_url and formed.payment_qr
            assert formed.formation_timeline_created and formed.formation_activity_created
            # Quiescent mode switch, unfinished payment retained. Redis is not used
            # by the following recovery handler, exactly as in compatible legacy.
            await registry.set_mode('legacy')
            legacy = PaymentEventRepository(sessions, expected_mode='legacy')
            assert await legacy.backfill() == 0
            await http.post(f'/_control/pay/{formed.bitrix_payment_id}')
            await legacy.receive_callback(formed.bitrix_payment_id)
            legacy_settings = settings.model_copy(update={'payment_processing_mode': 'legacy'})
            recovery = PaymentRecovery(legacy,
                PaymentJobExecutor(services, legacy, legacy_settings, 'legacy-reconciliation'), 'reconciliation')
            assert await recovery.once() == 1
            paid = await payments.get(tx.id)
            assert paid.status == 'paid' and paid.paid_timeline_created and paid.paid_activity_created
            assert paid.bitrix_invoice_id == formed.bitrix_invoice_id
            assert paid.bitrix_payment_id == formed.bitrix_payment_id
            await registry.set_mode('events')
            assert await queue.backfill() == 0
            # Replay the old formation event after switching back: no CRM effects.
            before = (await http.get('/_control/state')).json()
            await stream.publish(event)
            await consumer.handle(*(await stream.read('events-formation', block_ms=10))[0])
            after = (await http.get('/_control/state')).json()
            assert before == after
            assert {key: len(rows) for key, rows in after['tables'].items()} == {
                'invoices': 1, 'rows': 1, 'payments': 1, 'products': 1, 'comments': 2, 'activities': 2}
            assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
    finally:
        await redis.delete(stream.key)
        await redis.aclose()
