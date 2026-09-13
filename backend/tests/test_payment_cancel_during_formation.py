"""T20: queue cancellation while a real formation HTTP write is in flight."""
import asyncio
import os
from datetime import UTC, datetime
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
from app.payment_status import PaymentStatusHandler
from app.payments import PaymentService
from app.repositories import PaymentRepository
from runtime_fake_bitrix import create_app
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
async def test_cancel_waits_for_formation_owner_then_deletes_payment_once(event_db):
    queue, sessions, uid = event_db
    values = transaction_values(uid, now=datetime.now(UTC))
    values['email'] = None
    tx, _ = await queue.create_payment(uuid4(), values)
    settings = Settings(_env_file=None, payment_telemetry_enabled=False,
        bx24_webhook='http://fake/rest/1/test', bx24_payment_link_field='ufCrmTestLink',
        bx24_payment_send_trigger='stageId=TEST:SEND')
    payments = PaymentRepository(sessions, events=queue)
    entered, release = asyncio.Event(), asyncio.Event()
    delegate = httpx.ASGITransport(app=create_app())
    async def remote(request):
        result = await delegate.handle_async_request(request)
        if request.url.path.endswith('/crm.item.add.json'):
            entered.set()
            await release.wait()
        return result
    async with httpx.AsyncClient(transport=httpx.MockTransport(remote), base_url='http://fake') as http:
        bitrix = Bitrix24Client(settings, http)
        service = PaymentService(settings, payments, SimpleNamespace(), PaymentClientResolver(settings, bitrix), bitrix)
        services = SimpleNamespace(payment_repository=payments, payment_service=service, bitrix=bitrix,
            payment_status=PaymentStatusHandler(settings, payments, bitrix))
        formation = PaymentJobExecutor(services, queue, settings, 'formation')
        cancellation = PaymentJobExecutor(services, queue, settings, 'cancellation')
        async with sessions() as db:
            job = await db.scalar(select(PaymentJob))
        task = asyncio.create_task(formation.execute(job.id))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await queue.queue_command(tx.id, uid, 'cancel')
            async with sessions() as db:
                command = await db.scalar(select(PaymentJob).where(PaymentJob.state == 'ready'))
            before = (await http.get('/_control/state')).json()
            assert not await cancellation.execute(command.id)
            assert (await http.get('/_control/state')).json() == before
            assert await queue.pending_commands(tx.id) == ['cancel']
            release.set()
            assert await asyncio.wait_for(task, 3)
            # Formation may overwrite current_step, but queued cancellation persists.
            assert await queue.pending_commands(tx.id) == ['cancel']
            assert await cancellation.execute(command.id)
            result = await payments.get(tx.id)
            assert result.status == 'canceled'
            assert await queue.pending_commands(tx.id) == []
            after = (await http.get('/_control/state')).json()
            assert after['calls'].count('crm.item.add') == 1
            assert after['calls'].count('crm.item.payment.add') == 1
            assert after['calls'].count('crm.item.payment.delete') == 1
            assert not after['tables']['payments']
            assert await cancellation.execute(command.id)
            assert await formation.execute(job.id)
            assert (await http.get('/_control/state')).json() == after
        finally:
            release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
