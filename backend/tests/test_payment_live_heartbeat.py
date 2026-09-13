"""T06/T08/T15: live renewal protects a long job across both job kinds."""
import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import PaymentExecutionLease, PaymentJob
from app.payment_job_executor import PaymentJobExecutor
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
async def test_live_heartbeat_outlives_initial_lease_and_blocks_other_kind(event_db):
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions.begin() as db:
        formation = await db.scalar(select(PaymentJob))
        reconciliation = await repo.enqueue_in_session(db, tx.id, 'reconciliation')
    entered, release = asyncio.Event(), asyncio.Event()
    payments = PaymentRepository(sessions)
    calls = []
    class Service:
        async def process(self, tid):
            calls.append(tid)
            entered.set()
            await release.wait()
            return await payments.get(tid)
    settings = Settings(_env_file=None, payment_telemetry_enabled=False,
        payment_lease_seconds=0.6, payment_heartbeat_seconds=0.1)
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments,
        payment_service=Service()), repo, settings, 'live-worker')
    task = asyncio.create_task(executor.execute(formation.id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with sessions() as db:
            initial = await db.get(PaymentExecutionLease, tx.id)
            initial_deadline, token = initial.lease_until, initial.fencing_token
        # Let actual DB time pass the initial deadline; never edit the lease.
        async with asyncio.timeout(3):
            while True:
                async with sessions() as db:
                    now = await db.scalar(select(func.clock_timestamp()))
                if now > initial_deadline:
                    break
                await asyncio.sleep(0.05)
        assert not task.done()
        async with sessions() as db:
            current = await db.get(PaymentExecutionLease, tx.id)
            assert current.lease_until > now and current.lease_until > initial_deadline
            assert current.fencing_token == token
        assert await repo.claim(formation.id, 'duplicate-consumer', 1) is None
        assert await repo.claim(reconciliation.id, 'fallback-reconciliation', 1) is None
        release.set()
        assert await asyncio.wait_for(task, 2)
        next_claim = await repo.claim(reconciliation.id, 'fallback-reconciliation', 1)
        assert next_claim is not None
        assert await repo.finish(next_claim)
        assert calls == [tx.id]
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
