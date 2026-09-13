"""Stop admission between jobs without cancelling the in-flight business step."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.payment_consumer import PaymentConsumer
from app.payment_recovery import PaymentRecovery


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['formation', 'reconciliation'])
async def test_recovery_drains_current_job_only(kind):
    stop, entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(job_id):
        entered.set()
        await finish.wait()
    repo = SimpleNamespace(backfill=AsyncMock(), recover_callbacks=AsyncMock(),
        recover_publications=AsyncMock(), due_jobs=AsyncMock(return_value=['one', 'two']))
    executor = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    worker = PaymentRecovery(repo, executor, kind)
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(entered.wait(), 2)
    stop.set()
    assert not task.done()
    finish.set()
    await asyncio.wait_for(task, 2)
    executor.execute.assert_awaited_once_with('one')
    assert await worker.once(stop) == 0
    repo.due_jobs.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['reclaim', 'read'])
async def test_consumer_does_not_start_next_delivered_job_on_stop(source):
    stop, entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rows = [('1-0', {}), ('2-0', {})]
    stream = SimpleNamespace(ensure_group=AsyncMock(),
        reclaim=AsyncMock(return_value=('0-0', rows if source == 'reclaim' else [])),
        read=AsyncMock(return_value=rows))
    consumer = PaymentConsumer(stream, None, None, 'owner')
    async def handle(message_id, fields):
        entered.set()
        await finish.wait()
    consumer.handle = AsyncMock(side_effect=handle)
    task = asyncio.create_task(consumer.run(stop))
    await asyncio.wait_for(entered.wait(), 2)
    stop.set()
    assert not task.done()
    finish.set()
    await asyncio.wait_for(task, 2)
    consumer.handle.assert_awaited_once_with('1-0', {})
    if source == 'reclaim':
        stream.read.assert_not_awaited()
    await consumer.once(stop)
    stream.reclaim.assert_awaited_once()
