"""Opt-in, separate-process benchmark against disposable PostgreSQL and Redis.

PAYMENT_EVENTS_LOAD_TEST=1 plus the two PAYMENT_EVENTS_TEST_* URLs is required.
Only UUID test schemas/stream keys are used. No real Bitrix traffic is possible.
"""
import asyncio
import multiprocessing
import os
import math
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(
    os.getenv('PAYMENT_EVENTS_LOAD_TEST') != '1' or not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL')
    or not os.getenv('PAYMENT_EVENTS_TEST_REDIS_URL'), reason='explicit isolated load-test opt-in required',
)


def worker_process(database_url, redis_url, schema, prefix, owner, ready, go, stop, writes, fault=None, kind='formation'):
    async def run():
        import httpx
        from redis.asyncio import Redis
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.bitrix24_client import Bitrix24Client
        from app.config import Settings
        from app.payment_consumer import PaymentConsumer
        from app.payment_event_repository import PaymentEventRepository
        from app.payment_events import PaymentStream
        from app.payment_job_executor import PaymentJobExecutor
        from app.repositories import PaymentRepository

        engine = create_async_engine(database_url, connect_args={'server_settings': {'search_path': schema}})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        jobs = PaymentEventRepository(sessions)
        payments = PaymentRepository(sessions)
        redis = Redis.from_url(redis_url, decode_responses=True)
        stream = PaymentStream(redis, prefix, kind)
        lease_options = {'payment_lease_seconds': 2, 'payment_heartbeat_seconds': 0.3} if fault else {}
        settings = Settings(_env_file=None, bx24_webhook='https://fake.invalid/rest/1/test/', **lease_options)

        async def fake_http(request):
            import json
            # Fixed remote latency dominates this deliberately unconstrained budget.
            await asyncio.sleep(0.08)
            payload = json.loads(request.content)
            if request.url.path.endswith('/crm.item.payment.get.json'):
                writes.put((owner, 'status-read'))
                return httpx.Response(200, json={'result': {'paid': False}})
            if fault and request.url.path.endswith('/crm.contact.list.json'):
                origin = payload['filter']['=ORIGIN_ID']
                fault['calls'].append(('read', origin))
                remote_id = fault['remote'].get(origin)
                return httpx.Response(200, json={'result': [{'ID': remote_id}] if remote_id else []})
            fields = payload['fields']
            if fault:
                fault['remote'][fields['ORIGIN_ID']] = 42
                fault['calls'].append(('write', fields['ORIGIN_ID']))
            writes.put((owner, fields['ORIGIN_ID']))
            if fault and fault.get('hold_write'):
                # The remote write is accepted but its HTTP response never arrives.
                # Heartbeat remains live until the parent kills this process.
                await asyncio.Event().wait()
            return httpx.Response(200, json={'result': 42})

        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_http)) as http:
            bitrix = Bitrix24Client(settings, http)
            class Formation:
                async def process(self, transaction_id):
                    await bitrix.call('crm.contact.add', {'fields': {'ORIGIN_ID': str(transaction_id)}})
                    return await payments.get(transaction_id)
            class Reconciliation:
                async def handle(self, payment_id):
                    await bitrix.get_payment(payment_id)
                    return await payments.get_by_payment_id(payment_id)
            executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments,
                payment_service=Formation(), payment_status=Reconciliation(), bitrix=bitrix), jobs, settings, owner)
            consumer = PaymentConsumer(stream, jobs, executor, owner, reclaim_idle_ms=100 if fault else 90000)
            try:
                async with sessions() as session:
                    await session.execute(text('SELECT 1'))
                await stream.ensure_group()
                ready.put(owner)
                while not go.is_set() and not stop.is_set():
                    await asyncio.sleep(0.01)
                while not stop.is_set():
                    await consumer.once()
            finally:
                await redis.aclose()
                await engine.dispose()
    asyncio.run(run())


@pytest.mark.asyncio
async def test_t25_reconciliation_backlog_does_not_delay_new_formation(event_db):
    from redis.asyncio import Redis
    from app.models import PaymentJob
    from app.payment_events import PaymentStream
    from app.payment_outbox import OutboxRelay
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    payments = PaymentRepository(sessions)
    async with sessions() as session:
        schema = await session.scalar(text('SELECT current_schema()'))
    context = multiprocessing.get_context('spawn')
    ready, writes = context.Queue(), context.Queue()
    go, stop = context.Event(), context.Event()
    prefix = 'isolation-' + uuid4().hex
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    streams = {kind: PaymentStream(redis, prefix, kind) for kind in ('formation', 'reconciliation')}
    relay = OutboxRelay(repo, streams)
    processes = []
    try:
        old_ids = []
        for payment_id in range(1000, 1048):
            values = transaction_values(uid, now=datetime.now(UTC))
            values.update(status='canceled', current_step='canceled', bitrix_payment_id=payment_id)
            tx, _ = await payments.create_or_get(idempotency_key=uuid4(), values=values)
            old_ids.append(tx.id)
            async with sessions.begin() as session:
                await repo.enqueue_in_session(session, tx.id, 'reconciliation')
        while await relay.once(): pass
        for kind in streams:
            process = context.Process(target=worker_process, args=(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'],
                os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], schema, prefix, kind, ready, go, stop, writes, None, kind))
            processes.append(process)
            process.start()
        for _ in processes: await asyncio.to_thread(ready.get, True, 20)
        go.set()
        assert await asyncio.to_thread(writes.get, True, 10) == ('reconciliation', 'status-read')
        started = time.monotonic()
        new, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        assert await relay.once()
        async with asyncio.timeout(5):
            while True:
                async with sessions() as session:
                    job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == new.id))
                if job.state == 'completed': break
                assert all(process.is_alive() for process in processes)
                await asyncio.sleep(0.02)
        elapsed = time.monotonic() - started
        async with sessions() as session:
            remaining = await session.scalar(select(func.count()).select_from(PaymentJob).where(
                PaymentJob.transaction_id.in_(old_ids), PaymentJob.state != 'completed'))
        assert elapsed < 1 and remaining > 0, (elapsed, remaining)
        print(f'\nISOLATION_RESULT formation_seconds={elapsed:.3f} old_checks_remaining={remaining}')
    finally:
        stop.set(); go.set()
        for process in processes:
            await asyncio.to_thread(process.join, 3)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
        await redis.delete(*(stream.key for stream in streams.values()))
        await redis.aclose()
        ready.close(); writes.close()
    assert all(process.exitcode == 0 for process in processes)


@pytest.mark.asyncio
async def test_t09_t10_t23_sigkill_after_remote_write_recovers_without_duplicate(event_db):
    import signal
    from redis.asyncio import Redis
    from app.models import PaymentJob, PaymentExternalWrite
    from app.payment_events import PaymentStream
    from app.payment_outbox import OutboxRelay
    repo, sessions, uid = event_db
    async with sessions() as session:
        schema = await session.scalar(text('SELECT current_schema()'))
    context = multiprocessing.get_context('spawn')
    manager = context.Manager()
    remote, calls = manager.dict(), manager.list()
    ready, writes = context.Queue(), context.Queue()
    go, stop = context.Event(), context.Event()
    prefix = 'kill-' + uuid4().hex
    redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
    stream = PaymentStream(redis, prefix, 'formation')
    processes = []
    def spawn(owner, hold):
        process = context.Process(target=worker_process, args=(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'],
            os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], schema, prefix, owner, ready, go, stop, writes,
            {'remote': remote, 'calls': calls, 'hold_write': hold}))
        processes.append(process)
        process.start()
        return process
    try:
        tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        assert await OutboxRelay(repo, {'formation': stream}).once()
        first = spawn('doomed-worker', True)
        await asyncio.to_thread(ready.get, True, 20)
        go.set()
        assert await asyncio.to_thread(writes.get, True, 10) == ('doomed-worker', str(tx.id))
        async with sessions() as session:
            write = await session.scalar(select(PaymentExternalWrite).where(PaymentExternalWrite.transaction_id == tx.id))
            assert write.state == 'in_flight'
        started = time.monotonic()
        first.kill()  # Only the explicitly created test process; no application container.
        await asyncio.to_thread(first.join, 5)
        assert first.exitcode == -signal.SIGKILL
        replacement = spawn('replacement-worker', False)
        await asyncio.to_thread(ready.get, True, 20)
        async with asyncio.timeout(10):
            while True:
                async with sessions() as session:
                    job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
                pending = (await redis.xpending(stream.key, stream.group))['pending']
                if job.state == 'completed' and pending == 0: break
                assert replacement.is_alive()
                await asyncio.sleep(0.05)
        elapsed = time.monotonic() - started
        assert elapsed <= 8, elapsed  # 2s lease + finite reclaim loop + process startup margin.
        assert list(calls) == [('write', str(tx.id)), ('read', str(tx.id))]
        async with sessions() as session:
            write = await session.scalar(select(PaymentExternalWrite).where(PaymentExternalWrite.transaction_id == tx.id))
            assert write.state == 'confirmed' and write.remote_id == '42'
            assert write.fencing_token >= 2
        print(f'\nSIGKILL_RESULT recovery_seconds={elapsed:.3f} remote_writes=1 pending=0')
    finally:
        stop.set(); go.set()
        for process in processes:
            await asyncio.to_thread(process.join, 3)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
        await redis.delete(stream.key)
        await redis.aclose()
        ready.close(); writes.close()
        manager.shutdown()
    assert replacement.exitcode == 0, 'replacement did not stop gracefully'


@pytest.mark.asyncio
async def test_t23_independent_process_scaling_1_2_4(event_db):
    from redis.asyncio import Redis
    from app.models import PaymentJob, PaymentExternalWrite
    from app.payment_events import PaymentStream
    from app.payment_outbox import OutboxRelay
    repo, sessions, uid = event_db
    async with sessions() as session:
        schema = await session.scalar(text('SELECT current_schema()'))
    context = multiprocessing.get_context('spawn')
    measurements = {}
    idle_delays = []
    batch_size = 48
    for count in (1, 2, 4):
        prefix = 'load-' + uuid4().hex
        redis = Redis.from_url(os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'], decode_responses=True)
        stream = PaymentStream(redis, prefix, 'formation')
        relay = OutboxRelay(repo, {'formation': stream})
        transactions = []
        for _ in range(batch_size):
            tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
            transactions.append(tx.id)
        while await relay.once():
            pass
        ready, writes = context.Queue(), context.Queue()
        go, stop = context.Event(), context.Event()
        processes = [context.Process(target=worker_process, args=(
            os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], os.environ['PAYMENT_EVENTS_TEST_REDIS_URL'],
            schema, prefix, f'load-{count}-{i}', ready, go, stop, writes,
        )) for i in range(count)]
        try:
            for process in processes: process.start()
            for _ in processes: await asyncio.to_thread(ready.get, True, 20)
            started = time.monotonic()
            go.set()
            async with asyncio.timeout(30):
                while True:
                    async with sessions() as session:
                        completed = await session.scalar(select(func.count()).select_from(PaymentJob).where(
                            PaymentJob.transaction_id.in_(transactions), PaymentJob.state == 'completed'))
                    if completed == batch_size: break
                    assert all(process.is_alive() for process in processes), 'worker exited before completion'
                    await asyncio.sleep(0.025)
            measurements[count] = round(time.monotonic() - started, 3)
            observed = [await asyncio.to_thread(writes.get, True, 3) for _ in range(batch_size)]
            assert {tid for _, tid in observed} == {str(tid) for tid in transactions}
            assert len({owner for owner, _ in observed}) == count
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(PaymentExternalWrite).where(
                    PaymentExternalWrite.transaction_id.in_(transactions), PaymentExternalWrite.state == 'confirmed')) == batch_size
            if count == 1:
                relay_stop = asyncio.Event()
                relay_task = asyncio.create_task(relay.run(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], relay_stop))
                try:
                    async with asyncio.timeout(40):
                        for _ in range(100):
                            # Before the transaction: a conservative upper bound
                            # on commit -> lease acquisition, not a QR measurement.
                            before_commit = datetime.now(UTC)
                            tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=before_commit))
                            while True:
                                async with sessions() as session:
                                    job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
                                if job.state == 'completed': break
                                assert processes[0].is_alive()
                                await asyncio.sleep(0.01)
                            idle_delays.append((job.started_at - before_commit).total_seconds())
                            _, written_id = await asyncio.to_thread(writes.get, True, 3)
                            assert written_id == str(tx.id)
                finally:
                    relay_stop.set()
                    await asyncio.wait_for(relay_task, timeout=3)
        finally:
            stop.set(); go.set()
            for process in processes:
                if process.pid is None: continue
                await asyncio.to_thread(process.join, 3)
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 3)
            await redis.delete(stream.key)
            await redis.aclose()
            ready.close(); writes.close()
        assert all(process.exitcode == 0 for process in processes), 'graceful shutdown failed'
    print(f'\nSCALING_RESULT batch={batch_size} fake_http_ms=80 seconds={measurements}')
    p95 = sorted(idle_delays)[math.ceil(len(idle_delays) * 0.95) - 1]
    print(f'IDLE_RESULT samples={len(idle_delays)} p95_start_upper_bound_seconds={p95:.3f}')
    assert len(idle_delays) == 100 and p95 <= 1.0
    assert measurements[2] <= measurements[1] * 0.7, measurements
    assert measurements[4] < measurements[2], measurements
