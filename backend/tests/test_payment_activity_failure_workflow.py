"""T17/T18: activity HTTP 400 after QR, delayed retry without duplicate formation."""
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentJob
from app.payment_client_resolver import PaymentClientResolver
from app.payment_job_executor import PaymentJobExecutor
from app.payments import PaymentService
from app.repositories import PaymentRepository
from runtime_fake_bitrix import create_app
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'error': ''}, {'result': None}])
async def test_activity_400_preserves_qr_and_retry_only_finishes_missing_step(event_db, body):
    queue, sessions, uid = event_db
    values = transaction_values(uid, now=datetime.now(UTC))
    values['email'] = None
    tx, _ = await queue.create_payment(uuid4(), values)
    payments = PaymentRepository(sessions, events=queue)
    settings = Settings(_env_file=None, payment_telemetry_enabled=False,
        bx24_webhook='http://fake/rest/1/test', bx24_payment_link_field='ufCrmTestLink',
        bx24_payment_send_trigger='stageId=TEST:SEND', bx24_payment_create_activity=True)
    delegate = httpx.ASGITransport(app=create_app())
    failed = False
    async def remote(request):
        nonlocal failed
        if request.url.path.endswith('/crm.activity.add.json') and not failed:
            failed = True
            return httpx.Response(400, json=body)
        return await delegate.handle_async_request(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(remote), base_url='http://fake') as http:
        bitrix = Bitrix24Client(settings, http)
        service = PaymentService(settings, payments, SimpleNamespace(), PaymentClientResolver(settings, bitrix), bitrix)
        executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments,
            payment_service=service, bitrix=bitrix), queue, settings, 'activity-test')
        async with sessions() as db:
            initial = await db.scalar(select(PaymentJob))
        assert await executor.execute(initial.id)
        partial = await payments.get(tx.id)
        assert failed and partial.payment_url and partial.payment_qr
        assert partial.formation_timeline_created and not partial.formation_activity_created
        assert not await queue.unknown_writes(tx.id)
        assert (await queue.get_job(initial.id)).state == 'superseded'
        async with sessions() as db:
            retry = await db.scalar(select(PaymentJob).where(PaymentJob.kind == 'formation', PaymentJob.state == 'ready'))
            assert retry.attempt_count == 1
        assert not await executor.execute(retry.id)  # available_at still in the future
        before = (await http.get('/_control/state')).json()
        async with sessions.begin() as db:
            (await db.get(PaymentJob, retry.id)).available_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await executor.execute(retry.id)
        result = await payments.get(tx.id)
        assert result.payment_qr == partial.payment_qr and result.formation_activity_created
        assert result.bitrix_invoice_id == partial.bitrix_invoice_id
        assert result.bitrix_payment_id == partial.bitrix_payment_id
        after = (await http.get('/_control/state')).json()
        for name in ('invoices', 'rows', 'payments', 'products', 'comments'):
            assert before['tables'][name] == after['tables'][name]
        assert len(after['tables']['activities']) == 1
        assert before['calls'].count('crm.item.update') == after['calls'].count('crm.item.update')
        assert (await queue.get_job(retry.id)).state == 'completed'
