"""Lost HTTP responses, durable write journal, replacement executor and visibility lag."""
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.errors import Bitrix24Error
from app.models import PaymentJob, PaymentExecutionLease
from app.payment_execution import current_execution, PaymentExecution
from app.payment_job_executor import PaymentJobExecutor
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values
from test_payment_write_recovery import CASES

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('method,params,reader,present,absent,expected', CASES[:6], ids=[c[0] for c in CASES[:6]])
@pytest.mark.parametrize('delayed', [False, True, 'never-visible'])
async def test_lost_create_response_recovers_without_second_write(event_db, method, params, reader, present, absent, expected, delayed):
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as db:
        job = await db.scalar(select(PaymentJob))
    old = await repo.claim(job.id, 'interrupted', 30)
    calls = []
    visible = not delayed
    def remote(request):
        called = request.url.path.rsplit('/', 1)[-1].removesuffix('.json')
        calls.append(called)
        if called == method:
            raise httpx.ReadTimeout('test accepted remote write; response lost')
        assert called.endswith(('.list', '.get'))
        return httpx.Response(200, json={'result': present if visible else absent})
    async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as http:
        settings = Settings(_env_file=None, bx24_webhook='https://fake.invalid/rest/1/test/')
        bitrix = Bitrix24Client(settings, http)
        token = current_execution.set(PaymentExecution(repo, old))
        try:
            with pytest.raises(Bitrix24Error):
                await bitrix.call(method, params)
        finally:
            current_execution.reset(token)
        assert len(await repo.unknown_writes(tx.id)) == 1
        async with sessions.begin() as db:
            (await db.get(PaymentExecutionLease, tx.id)).lease_until = datetime.now(UTC) - timedelta(seconds=1)
        payments = PaymentRepository(sessions)
        processed = []
        class Service:
            async def process(self, tid):
                result = await bitrix.call(method, params)
                assert result == expected
                processed.append(tid)
                return await payments.get(tid)
        executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments,
            payment_service=Service(), bitrix=bitrix), repo, settings, 'replacement')
        assert await executor.execute(job.id)
        if delayed:
            assert not processed
            assert len(await repo.unknown_writes(tx.id)) == 1
            async with sessions.begin() as db:
                assert (await db.get(PaymentJob, job.id)).state == 'superseded'
                retry = await db.scalar(select(PaymentJob).where(PaymentJob.state == 'ready'))
                assert retry.attempt_count == 1
                retry.available_at = datetime.now(UTC) - timedelta(seconds=1)
            if delayed == 'never-visible':
                for attempt in range(2, 5):
                    assert await executor.execute(retry.id)
                    if attempt < 4:
                        async with sessions.begin() as db:
                            retry = await db.scalar(select(PaymentJob).where(PaymentJob.state == 'ready'))
                            retry.available_at = datetime.now(UTC) - timedelta(seconds=1)
                assert (await repo.get_job(retry.id)).state == 'needs_reconciliation'
                assert len(await repo.unknown_writes(tx.id)) == 1
                assert not processed
                assert calls.count(method) == 1
                assert len(calls) == 5  # one write and four read-only recovery attempts
                return
            visible = True
            assert await executor.execute(retry.id)
            job = retry
        assert processed == [tx.id]
        assert not await repo.unknown_writes(tx.id)
        assert (await repo.get_job(job.id)).state == 'completed'
    assert calls.count(method) == 1
    assert len(calls) == (3 if delayed else 2)
