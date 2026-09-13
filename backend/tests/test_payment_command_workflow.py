"""T20: HTTP admission -> durable job -> Redis -> real payment service, fake CRM."""
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.main import app, settings as api_settings
from app.models import PaymentJob, PaymentOutbox
from app.payment_client_resolver import PaymentClientResolver
from app.payment_consumer import PaymentConsumer
from app.payment_events import PaymentEvent, PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.payment_status import PaymentStatusHandler
from app.payments import PaymentService
from app.repositories import PaymentRepository
from runtime_fake_bitrix import create_app
from test_payment_events_repository import event_db
from test_payments_api import FakeSessionStore, session, payment_payload

pytestmark = pytest.mark.skipif(not all(os.getenv(key) for key in (
    'PAYMENT_EVENTS_TEST_DATABASE_URL', 'PAYMENT_EVENTS_TEST_REDIS_URL')),
    reason='isolated PostgreSQL and Redis required')


@pytest.mark.asyncio
@pytest.mark.parametrize('action,paid_before_execution', [('resend', False), ('cancel', False), ('cancel', True), ('select', False), ('resume', False)])
async def test_http_command_durable_execution_and_replay(event_db, monkeypatch, action, paid_before_execution):
    queue, sessions, uid = event_db
    authenticated = session().model_copy(update={'internal_user_id': uid})
    store = FakeSessionStore(authenticated)
    settings = Settings(_env_file=None, payment_telemetry_enabled=False,
        bx24_webhook='http://fake/rest/1/test', bx24_payment_link_field='ufCrmTestLink',
        bx24_payment_send_trigger='stageId=TEST:SEND')
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, 'events-test-' + uuid4().hex, 'formation')
    payments = PaymentRepository(sessions, events=queue)
    class Catalog:
        async def get(self, product_id):
            return SimpleNamespace(product_id=product_id, title='Test', default_amount=Decimal('1500'), currency='RUB')
    try:
        fake = create_app(contact_ids=(5, 6) if action == 'select' else (5,))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake), base_url='http://fake') as remote:
            bitrix = Bitrix24Client(settings, remote)
            service = PaymentService(settings, payments, Catalog(), PaymentClientResolver(settings, bitrix), bitrix)
            monkeypatch.setattr(app.state, 'payment_service', service, raising=False)
            monkeypatch.setattr(app.state, 'session_store', store, raising=False)
            services = SimpleNamespace(payment_repository=payments, payment_service=service,
                payment_status=PaymentStatusHandler(settings, payments, bitrix), bitrix=bitrix)
            consumer = PaymentConsumer(stream, queue,
                PaymentJobExecutor(services, queue, settings, 'workflow'), 'workflow')
            await stream.ensure_group()
            async def deliver_ready():
                async with sessions() as db:
                    job = await db.scalar(select(PaymentJob).where(PaymentJob.kind == 'formation',
                        PaymentJob.state == 'ready'))
                    assert job is not None
                    outbox = await db.scalar(select(PaymentOutbox).where(PaymentOutbox.job_id == job.id))
                event = PaymentEvent(event_id=outbox.event_id, job_id=job.id, transaction_id=job.transaction_id,
                    event_type='payment.formation_requested', generation=job.generation)
                await stream.publish(event)
                await consumer.handle(*(await stream.read('workflow', block_ms=10))[0])
                assert (await queue.get_job(job.id)).state == 'completed'
                return event
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url='http://test', cookies={api_settings.session_cookie_name: 'sid'},
                    headers={'X-CSRF-Token': 'csrf-test'}) as client:
                response = await client.post('/api/payments', json=payment_payload())
                assert response.status_code == 202
                tid = UUID(response.json()['id'])
                if action == 'resume':
                    # Persist the executor's terminal failure transition. External
                    # fault injection itself is covered by the outage/write tests.
                    async with sessions() as db:
                        stopped = await db.scalar(select(PaymentJob))
                    claim = await queue.claim(stopped.id, 'failed-worker', 30)
                    assert await queue.finish(claim, state='failed', error='PAYMENT_PROCESSING_FAILED')
                else:
                    await deliver_ready()
                formed = (await client.get(f'/api/payments/{tid}')).json()
                if action == 'select':
                    assert formed['status'] == 'client_selection_required' and not formed['payment_qr']
                    assert len((await payments.get(tid)).candidate_snapshot) == 2
                elif action == 'resume':
                    assert formed['status'] == 'failed' and not formed['payment_qr']
                else:
                    assert formed['status'] == 'send_queued' and formed['payment_qr']
                baseline = (await remote.get('/_control/state')).json()
                endpoint = f'/api/payments/{tid}/' + ('client-selection' if action == 'select' else action)
                if action == 'resume':
                    endpoint = f'/api/admin/payments/{tid}/resume'
                body = {'entity_type': 'contact', 'entity_id': 6} if action == 'select' else {}
                if action == 'select':
                    invalid = await client.post(endpoint, json={'entity_type': 'contact', 'entity_id': 999})
                    assert invalid.status_code == 409
                denied = await client.post(endpoint, json=body, headers={'X-CSRF-Token': 'wrong'})
                assert denied.status_code == 403
                assert await queue.pending_commands(tid) == []
                # Another authenticated employee cannot command this payment.
                store.session = authenticated.model_copy(update={'internal_user_id': uuid4()})
                assert (await client.post(endpoint, json=body)).status_code in {403, 404}
                store.session = authenticated
                if action == 'resume':
                    assert (await client.post(endpoint)).status_code == 403
                    store.session = authenticated.model_copy(update={
                        'user': authenticated.user.model_copy(update={'status': 'admin'})})
                accepted = await client.post(endpoint, json=body)
                assert accepted.status_code == 200
                assert accepted.json()['pending_commands'] == [action]
                repeated = await client.post(endpoint, json=body)
                if action in {'select', 'resume'}:
                    assert repeated.status_code == 409  # choice is already durably queued
                else:
                    assert repeated.status_code == 200 and repeated.json()['pending_commands'] == [action]
                async with sessions() as db:
                    queued = list(await db.scalars(select(PaymentJob).where(
                        PaymentJob.kind == 'formation', PaymentJob.state == 'ready')))
                    assert len(queued) == 1
                    if action == 'select':
                        assert queued[0].details['selection'] == ['contact', 6]
                assert (await client.get(f'/api/payments/{tid}')).json()['pending_commands'] == [action]
                assert (await remote.get('/_control/state')).json() == baseline
                if paid_before_execution:
                    tx = await payments.get(tid)
                    await remote.post(f'/_control/pay/{tx.bitrix_payment_id}')
                event = await deliver_ready()
                result = (await client.get(f'/api/payments/{tid}')).json()
                assert result['pending_commands'] == []
                expected = 'paid' if paid_before_execution else 'canceled' if action == 'cancel' else 'send_queued'
                assert result['status'] == expected
                if action == 'select':
                    tx = await payments.get(tid)
                    assert tx.bitrix_contact_id == 6 and result['payment_qr']
                    assert tx.client_resolution == {'type': 'contact', 'contact_id': 6}
                if action == 'resume':
                    assert result['payment_qr']
                    assert (await queue.get_job(stopped.id)).state == 'superseded'
                after = (await remote.get('/_control/state')).json()
                assert len(after['tables']['invoices']) == 1
                assert after['calls'].count('crm.item.payment.add') == 1
                assert after['calls'].count('crm.item.payment.delete') == (1 if action == 'cancel' and not paid_before_execution else 0)
                if action == 'resend':
                    assert after['calls'].count('crm.item.update') == baseline['calls'].count('crm.item.update') + 1
                    assert result['payment_qr'] == formed['payment_qr']
                if paid_before_execution:
                    tx = await payments.get(tid)
                    assert tx.paid_timeline_created and tx.paid_activity_created
                # Redelivery after commit must ACK without rerunning the command.
                await stream.publish(event)
                await consumer.handle(*(await stream.read('workflow', block_ms=10))[0])
                assert (await remote.get('/_control/state')).json() == after
                assert (await redis.xpending(stream.key, stream.group))['pending'] == 0
    finally:
        await redis.delete(stream.key)
        await redis.aclose()
