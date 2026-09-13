"""T05/T13: ready work arriving just after an empty polling pass."""
import asyncio
import os
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.payment_job_executor import PaymentJobExecutor
from app.payment_outbox import OutboxRelay
from app.payment_recovery import PaymentRecovery
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['relay', 'fallback'])
async def test_polling_detects_post_scan_arrival_within_interval_and_storage_margin(event_db, monkeypatch, kind):
    repo, sessions, uid = event_db
    interval = 0.2
    empty_scan, received, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []
    if kind == 'relay':
        class Stream:
            async def publish(self, event):
                observed.append((event.transaction_id, perf_counter()))
                received.set()
                return '1-0'
        worker = OutboxRelay(repo, {'formation': Stream()}, poll_seconds=interval)
        async def lost_notifications(*args):
            await stop.wait()
        monkeypatch.setattr(worker, 'listen', lost_notifications)
    else:
        payments = PaymentRepository(sessions)
        class Service:
            async def process(self, tid):
                observed.append((tid, perf_counter()))
                received.set()
                return await payments.get(tid)
        executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments, payment_service=Service()),
            repo, Settings(_env_file=None, payment_telemetry_enabled=False), 'deadline-fallback')
        worker = PaymentRecovery(repo, executor, 'formation', interval=interval)
    original_once = worker.once
    async def once(*args):
        result = await original_once(*args)
        if not result:
            empty_scan.set()
        return result
    monkeypatch.setattr(worker, 'once', once)
    coroutine = worker.run(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], stop) if kind == 'relay' else worker.run(stop)
    task = asyncio.create_task(coroutine)
    try:
        await asyncio.wait_for(empty_scan.wait(), 2)
        started = perf_counter()  # Conservative upper bound: includes commit cost.
        tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        await asyncio.wait_for(received.wait(), interval + 0.5)
        assert observed[0][0] == tx.id
        latency = observed[0][1] - started
        assert 0 <= latency <= interval + 0.5
        print(f'{kind}: interval={interval:.3f}s admission-to-detection upper bound={latency:.3f}s; storage/scheduling margin=0.500s')
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
