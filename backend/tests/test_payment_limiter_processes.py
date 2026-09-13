"""Verify a common limiter budget across OS processes without Redis."""
import asyncio
import multiprocessing
import os
import time

import pytest
from sqlalchemy import text
from test_payment_events_repository import event_db

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


def acquire_process(url, schema, role, ready, start, results, duration=None):
    async def run():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.payment_event_repository import PaymentEventRepository
        from app.payment_limiter import PaymentRequestLimiter
        from app.config import Settings
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
        try:
            repo = PaymentEventRepository(async_sessionmaker(engine, expire_on_commit=False))
            limiter = PaymentRequestLimiter(repo, Settings(_env_file=None,
                bx24_webhook='https://fake.invalid/rest/1/test/', payment_bitrix_requests_per_second=20))
            ready.set()
            await asyncio.to_thread(start.wait, 15)
            if duration is None:
                for _ in range(8):
                    await limiter.acquire()
                    results.put((role, time.monotonic()))
            else:
                try:
                    async with asyncio.timeout(duration):
                        while True:
                            await limiter.acquire()
                            results.put((role, time.monotonic()))
                except TimeoutError:
                    pass
                results.put((role, None))
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.asyncio
async def test_shared_budget_across_three_independent_processes(event_db):
    _, sessions, _ = event_db
    async with sessions() as db:
        schema = await db.scalar(text('SELECT current_schema()'))
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Event()
    results = ctx.Queue()
    processes = []
    try:
        for role in ('formation', 'reconciliation', 'fallback'):
            ready = ctx.Event()
            process = ctx.Process(target=acquire_process, args=(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'],
                schema, role, ready, start, results))
            process.start()
            processes.append(process)
            assert await asyncio.to_thread(ready.wait, 15)
        start.set()
        samples = [await asyncio.to_thread(results.get, True, 15) for _ in range(24)]
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            assert process.exitcode == 0
        times = sorted(at for _, at in samples)
        # Each process requesting 20 RPS must not create a combined 60 RPS budget.
        assert times[-1] - times[0] >= 23 / 20 - 0.02
        assert {role: sum(r == role for r, _ in samples) for role in
                ('formation', 'reconciliation', 'fallback')} == dict(formation=8, reconciliation=8, fallback=8)
        print({'requests': 24, 'elapsed_seconds': round(times[-1] - times[0], 3), 'redis_used': False})
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
        results.close()
        results.join_thread()


@pytest.mark.asyncio
@pytest.mark.parametrize('formation_count', [1, 4])
async def test_sustained_contention_all_roles_make_progress(event_db, formation_count):
    _, sessions, _ = event_db
    async with sessions() as db:
        schema = await db.scalar(text('SELECT current_schema()'))
    ctx = multiprocessing.get_context('spawn')
    start, results = ctx.Event(), ctx.Queue()
    processes, samples = [], []
    roles = tuple(f'formation-{i}' for i in range(formation_count)) + ('reconciliation', 'fallback')
    try:
        for role in roles:
            ready = ctx.Event()
            process = ctx.Process(target=acquire_process, args=(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'],
                schema, role, ready, start, results, 6))
            process.start()
            processes.append(process)
            assert await asyncio.to_thread(ready.wait, 15)
        began = time.monotonic()
        start.set()
        completed = set()
        while len(completed) < len(roles):
            role, at = await asyncio.to_thread(results.get, True, 15)
            if at is None:
                completed.add(role)
            else:
                samples.append((role, at))
        metrics = {}
        for role in roles:
            times = [began] + sorted(at for r, at in samples if r == role) + [began + 6]
            metrics[role] = dict(count=len(times) - 2, max_gap=round(max(b-a for a, b in zip(times, times[1:])), 3))
        print(metrics)
        # A finite stress gate, not a mathematical FIFO guarantee.
        assert all(m['count'] >= 5 and m['max_gap'] < 3 for m in metrics.values()), metrics
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
        results.close()
        results.join_thread()
