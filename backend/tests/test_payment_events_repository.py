"""Real-Postgres tests for the outbox contract (T01/T02/T06/T08/T11/T15).

Use compose.payment-events-test.yaml, never the application database.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, User
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv("PAYMENT_EVENTS_TEST_DATABASE_URL"), reason="isolated events PostgreSQL required")


@pytest_asyncio.fixture
async def event_db():
    from app.payment_event_repository import PaymentEventRepository
    url = os.environ["PAYMENT_EVENTS_TEST_DATABASE_URL"]
    assert url.rsplit('/', 1)[-1] == 'payment_events_test', 'refusing non-test database'
    schema = 'events_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as c:
        await c.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with sessions.begin() as s:
        user = User(techportal_user_id='test', email='test@example.test', permissions={})
        s.add(user)
        await s.flush()
        user_id = user.id
    try:
        yield PaymentEventRepository(sessions), sessions, user_id
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [None, 'BX24_RATE_LIMITED'])
async def test_callback_during_running_check_keeps_followup_without_duplicate_poll(event_db, error):
    from app.models import PaymentJob, PaymentTransaction
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions.begin() as s:
        row = await s.get(PaymentTransaction, tx.id)
        row.bitrix_payment_id = 991
        formation = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    formation_claim = await repo.claim(formation.id, 'formation', 30)
    await repo.receive_callback(991)
    assert await repo.finish(formation_claim, next_kind='reconciliation', next_at=datetime.now(UTC) + timedelta(minutes=1))
    async with sessions() as s:
        pending = list(await s.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id, PaymentJob.state == 'ready')))
    assert len(pending) == 1
    claim = await repo.claim(pending[0].id, 'reconciliation', 30)
    assert claim is not None
    # The current check may already have read unpaid when the callback arrives.
    await repo.receive_callback(991)
    await repo.receive_callback(991)
    retry_at = datetime.now(UTC) + timedelta(minutes=1)
    assert await repo.finish(claim, state='superseded' if error else 'completed', error=error,
        next_kind='reconciliation', next_at=retry_at, attempt_count=2 if error else 0)
    async with sessions() as s:
        pending = list(await s.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id, PaymentJob.state == 'ready')))
    assert len(pending) == 1
    if error:
        assert pending[0].available_at >= retry_at and pending[0].attempt_count == 2
    else:
        assert pending[0].available_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_admin_retry_is_atomic_audited_and_preserves_unknown_write(event_db):
    from app.models import PaymentJob, PaymentExternalWrite, PaymentTransactionEvent
    from app.errors import PaymentStateError
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    claim = await repo.claim(job.id, 'old-worker', 30)
    await repo.begin_write(claim, 'test-marker', 'crm.contact.add')
    assert await repo.finish(claim, state='needs_reconciliation', error='EXTERNAL_WRITE_UNKNOWN')
    results = await asyncio.gather(repo.retry_job(job.id, uid), repo.retry_job(job.id, uid), return_exceptions=True)
    assert sum(isinstance(result, PaymentStateError) for result in results) == 1
    async with sessions() as s:
        queued = list(await s.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id, PaymentJob.state == 'ready')))
        assert len(queued) == 1
        assert (await s.get(PaymentJob, job.id)).state == 'superseded'
        write = await s.scalar(select(PaymentExternalWrite).where(PaymentExternalWrite.transaction_id == tx.id))
        assert write.state == 'in_flight'
        events = list(await s.scalars(select(PaymentTransactionEvent).where(PaymentTransactionEvent.transaction_id == tx.id,
            PaymentTransactionEvent.event_type == 'payment.job_retry_requested')))
        assert len(events) == 1
        assert events[0].safe_payload['admin_user_id'] == str(uid)
        assert events[0].safe_payload['new_job_id'] == str(queued[0].id)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('delivered', [False, True])
async def test_t27_executor_delivery_telemetry_is_opt_in_and_safe(event_db, enabled, delivered, monkeypatch):
    from types import SimpleNamespace
    from app.config import Settings
    from app.models import PaymentJob, PaymentOutbox
    from app.payment_job_executor import PaymentJobExecutor
    from app.payment_limiter import PaymentRequestLimiter
    from app.payment_telemetry import current_trace, current_parent, span
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    payments = PaymentRepository(sessions)
    settings = Settings(_env_file=None, payment_telemetry_enabled=enabled)
    limiter = PaymentRequestLimiter(repo, settings)
    class Service:
        async def process(self, tid):
            with span('fake_work'):
                await limiter.acquire()
            return await payments.get(tid)
    if not enabled:
        def forbidden(): raise AssertionError('Disabled telemetry must not allocate a trace')
        monkeypatch.setattr('app.payment_job_executor.Trace', forbidden)
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
        outbox = await s.scalar(select(PaymentOutbox).where(PaymentOutbox.job_id == job.id))
    received_at = datetime.now(UTC)
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments, payment_service=Service()), repo, settings, 'test-consumer')
    delivery = dict(event_id=outbox.event_id,
        stream_message_id=f'{int(received_at.timestamp() * 1000)}-0', received_at=received_at) if delivered else {}
    assert await executor.execute(job.id, **delivery)
    assert current_trace.get() is None and current_parent.get() is None
    rows = await payments.timing_detail(tx.id)
    if not enabled:
        assert rows == []
    else:
        names = {row.name for row in rows}
        assert {'job_execution', 'job_attempt', 'lease_acquire', 'job_ready_wait', 'limiter_wait', 'fake_work'} <= names
        assert ('outbox_wait' in names) == delivered
        assert ('transport_wait' in names) == delivered
        root = next(row for row in rows if row.name == 'job_execution')
        assert root.details == {'job_id': str(job.id), 'event_id': str(outbox.event_id) if delivered else None,
            'consumer': 'test-consumer', 'source': 'redis' if delivered else 'database'}
        assert len({row.run_id for row in rows}) == 1
        assert all(row.duration_ms >= 0 for row in rows)
        assert tx.phone_normalized not in str([row.details for row in rows])


@pytest.mark.asyncio
async def test_queue_health_reports_stalled_work_without_pii_or_live_write_false_alarm(event_db):
    from app.config import Settings
    from app.models import PaymentJob, PaymentOutbox
    from app.payment_health import queue_health
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    settings = Settings(_env_file=None, payment_processing_mode='events', payment_telemetry_enabled=False)
    async with sessions.begin() as s:
        job = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
        job.available_at = datetime.now(UTC) - timedelta(seconds=180)
        outbox = await s.scalar(select(PaymentOutbox).where(PaymentOutbox.job_id == job.id))
        outbox.available_at = job.available_at
    snapshot = await queue_health(sessions, settings)
    assert {'FORMATION_BACKLOG_STALLED', 'OUTBOX_PUBLICATION_STALLED'} <= set(snapshot['alerts'])
    assert snapshot['backlog'][0]['count'] == 1
    assert snapshot['backlog'][0]['oldest_due_seconds'] >= 180
    claim = await repo.claim(job.id, 'health-test', 30)
    await repo.begin_write(claim, 'test-health', 'crm.contact.add')
    snapshot = await queue_health(sessions, settings)
    assert snapshot['active_leases'] == 1 and snapshot['unknown_writes'] == 0
    assert 'FORMATION_BACKLOG_STALLED' not in snapshot['alerts']
    await repo.finish(claim, state='needs_reconciliation', error='EXTERNAL_WRITE_UNKNOWN')
    snapshot = await queue_health(sessions, settings)
    assert snapshot['unknown_writes'] == 1 and snapshot['unpublished'] == 0
    assert {'EXTERNAL_WRITES_NEED_RECONCILIATION', 'JOBS_REQUIRE_ATTENTION'} <= set(snapshot['alerts'])
    assert str(tx.id) not in str(snapshot) and tx.phone_normalized not in str(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,details', [
    ('formation', {}),
    ('formation', {'action': 'cancel', 'scope': 'cancel:test'}),
    ('formation', {'action': 'select', 'selection': ['lead', 123]}),
    ('reconciliation', {}),
])
async def test_transaction_resume_preserves_failed_job_intent(event_db, kind, details):
    from app.models import PaymentJob
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions.begin() as s:
        first = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
        first.state = 'superseded'
        failed = await repo.enqueue_in_session(s, tx.id, kind, details=details)
    claim = await repo.claim(failed.id, 'test-worker', 30)
    assert await repo.finish(claim, state='failed', error='PAYMENT_PROCESSING_FAILED')
    await repo.queue_command(tx.id, uid, 'resume', admin=True)
    async with sessions() as s:
        queued = list(await s.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id, PaymentJob.state == 'ready')))
        assert len(queued) == 1
        assert queued[0].kind == kind and queued[0].details == {'action': 'resume', **details}
        assert (await s.get(PaymentJob, failed.id)).state == 'superseded'
    assert await repo.pending_commands(tx.id) == [details.get('action', 'resume')]


@pytest.mark.asyncio
async def test_transaction_resume_does_not_guess_between_failed_commands(event_db):
    from app.models import PaymentJob
    from app.errors import PaymentStateError
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        first = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    claim = await repo.claim(first.id, 'worker', 30)
    assert await repo.finish(claim, state='failed', error='PAYMENT_PROCESSING_FAILED')
    async with sessions.begin() as s:
        other = await repo.enqueue_in_session(s, tx.id, 'formation', details={'action': 'cancel'})
        other.state = 'failed'
    with pytest.raises(PaymentStateError):
        await repo.queue_command(tx.id, uid, 'resume', admin=True)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.state == 'ready')) == 0
        assert await s.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.state == 'failed')) == 2


@pytest.mark.asyncio
async def test_executor_uses_retry_budget_from_claimed_job_not_preclaim_snapshot(event_db, monkeypatch):
    from types import SimpleNamespace
    from app.config import Settings
    from app.models import PaymentJob
    from app.payment_job_executor import PaymentJobExecutor
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as session:
        job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    original = repo.claim
    async def claim_with_updated_budget(*args):
        async with sessions.begin() as session:
            (await session.get(PaymentJob, job.id)).attempt_count = 2
        return await original(*args)
    monkeypatch.setattr(repo, 'claim', claim_with_updated_budget)
    class Failure:
        async def process(self, tid): raise RuntimeError('injected temporary error')
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=PaymentRepository(sessions), payment_service=Failure()),
        repo, Settings(_env_file=None, bx24_worker_max_retries=2), 'budget-test')
    assert await executor.execute(job.id)
    assert (await repo.get_job(job.id)).state == 'failed'


@pytest.mark.asyncio
async def test_pending_commands_survive_step_overwrite_and_clear_on_completion(event_db):
    from app.models import PaymentJob, PaymentTransaction
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    await repo.queue_command(tx.id, uid, 'cancel')
    async with sessions.begin() as session:
        (await session.get(PaymentTransaction, tx.id)).current_step = 'invoice_created'
    assert await repo.pending_commands(tx.id) == ['cancel']
    async with sessions() as session:
        jobs = list(await session.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id)))
    command = next(job for job in jobs if job.details.get('action') == 'cancel')
    claim = await repo.claim(command.id, 'command-worker', 30)
    assert await repo.finish(claim)
    assert await repo.pending_commands(tx.id) == []


@pytest.mark.asyncio
async def test_t01_t02_atomic_create_and_concurrent_idempotency(event_db):
    from app.models import PaymentJob, PaymentOutbox, PaymentTransaction
    repo, sessions, uid = event_db
    key = uuid4()
    values = transaction_values(uid, now=datetime.now(UTC))
    a, b = await asyncio.gather(*(repo.create_payment(key, values) for _ in range(2)))
    assert a[0].id == b[0].id
    assert sum((a[1], b[1])) == 1
    async with sessions() as s:
        for model in (PaymentTransaction, PaymentJob, PaymentOutbox):
            assert await s.scalar(select(func.count()).select_from(model)) == 1


@pytest.mark.asyncio
async def test_t01_rollback_includes_payment_job_outbox(event_db, monkeypatch):
    from app.models import PaymentJob, PaymentOutbox, PaymentTransaction
    repo, sessions, uid = event_db
    original = repo.enqueue_in_session
    async def broken(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('injected before commit')
    monkeypatch.setattr(repo, 'enqueue_in_session', broken)
    with pytest.raises(RuntimeError):
        await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        for model in (PaymentTransaction, PaymentJob, PaymentOutbox):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_t06_t08_t15_exclusive_lease_and_fencing(event_db):
    from app.models import PaymentJob, PaymentExecutionLease
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    claims = await asyncio.gather(repo.claim(job.id, 'consumer', 30), repo.claim(job.id, 'fallback', 30))
    assert sum(c is not None for c in claims) == 1
    claim = next(c for c in claims if c)
    assert await repo.renew(claim, 30)
    async with sessions.begin() as s:
        lease = await s.get(PaymentExecutionLease, tx.id)
        lease.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    replacement = await repo.claim(job.id, 'replacement', 30)
    assert replacement.token > claim.token
    assert not await repo.renew(claim, 30)
    assert not await repo.finish(claim)
    assert await repo.finish(replacement)
    assert await repo.claim(job.id, 'duplicate', 30) is None


@pytest.mark.asyncio
async def test_t06_different_payments_can_be_claimed_concurrently(event_db):
    from app.models import PaymentJob
    repo, sessions, uid = event_db
    for _ in range(2):
        await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        jobs = list(await s.scalars(select(PaymentJob)))
    claims = await asyncio.gather(*(repo.claim(j.id, str(j.id), 30) for j in jobs))
    assert all(claims)


@pytest.mark.asyncio
async def test_t03_t04_publish_lease_recovery_is_not_job_completion(event_db):
    from app.models import PaymentJob, PaymentOutbox
    repo, sessions, uid = event_db
    await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    first, second = await asyncio.gather(repo.claim_outbox(30), repo.claim_outbox(30))
    assert sum(item is not None for item in (first, second)) == 1
    row = first or second
    assert await repo.published(row.event_id, row.publisher_token, '1-0')
    async with sessions() as s:
        job = await s.get(PaymentJob, row.job_id)
        event = await s.get(PaymentOutbox, row.event_id)
        assert job.state == 'ready'
        assert event.completed_at is None
    assert await repo.claim_outbox(30) is None


@pytest.mark.asyncio
async def test_t04_relay_republishes_same_event_after_commit_failure(event_db, monkeypatch):
    from app.models import PaymentOutbox
    from app.payment_outbox import OutboxRelay
    repo, sessions, uid = event_db
    await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    messages = []
    class Stream:
        async def publish(self, event): messages.append(event); return f'{len(messages)}-0'
    relay = OutboxRelay(repo, {'formation': Stream()})
    original = repo.published
    async def lost_commit(*args): raise RuntimeError('injected after XADD')
    monkeypatch.setattr(repo, 'published', lost_commit)
    await relay.once()
    assert len(messages) == 1
    async with sessions.begin() as s:
        row = await s.scalar(select(PaymentOutbox))
        assert row.last_published_at is None
        row.next_publish_at = datetime.now(UTC) - timedelta(seconds=1)
    monkeypatch.setattr(repo, 'published', original)
    await relay.once()
    assert messages[0] == messages[1]
    assert await repo.valid_event(messages[0])
    assert not await repo.valid_event(messages[0].model_copy(update={'transaction_id': uuid4()}))


@pytest.mark.asyncio
async def test_t12_quarantine_is_durable_idempotent_and_contains_no_raw_payload(event_db):
    from app.models import PaymentEventQuarantine
    repo, sessions, _ = event_db
    await repo.quarantine('test', '1-0', 'INVALID_ENVELOPE')
    await repo.quarantine('test', '1-0', 'INVALID_ENVELOPE')
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentEventQuarantine)) == 1
    with pytest.raises(ValueError): await repo.quarantine('test', '2-0', 'raw secret')


@pytest.mark.asyncio
async def test_t05_listener_reconnect_catches_event_committed_while_disconnected(event_db, monkeypatch):
    from types import SimpleNamespace
    import asyncpg
    import app.payment_outbox as module
    repo, sessions, uid = event_db
    connections = []
    reconnecting, allow_reconnect = asyncio.Event(), asyncio.Event()
    async def connect(*args, **kwargs):
        if connections:
            reconnecting.set()
            await allow_reconnect.wait()
        connection = await asyncpg.connect(*args, **kwargs)
        connections.append(connection)
        return connection
    # Replace only the relay's module reference, not SQLAlchemy's asyncpg driver.
    monkeypatch.setattr(module, 'asyncpg', SimpleNamespace(connect=connect))
    delivered = []
    class Stream:
        async def publish(self, event): delivered.append(event); return '1-0'
    relay = module.OutboxRelay(repo, {'formation': Stream()}, poll_seconds=0.02)
    wake, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(relay.listen(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], wake, stop))
    try:
        await asyncio.wait_for(wake.wait(), timeout=2)
        wake.clear()
        assert len(connections) == 1
        # Terminate precisely the dedicated connection this test just created.
        pid = connections[0].get_server_pid()
        async with sessions() as session:
            assert await session.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
        await asyncio.wait_for(reconnecting.wait(), timeout=2)
        assert connections[0].is_closed()
        tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        assert not wake.is_set()  # NOTIFY had no subscriber and is not durable.
        allow_reconnect.set()
        await asyncio.wait_for(wake.wait(), timeout=2)
        assert len(connections) == 2 and connections[1].get_server_pid() != pid
        assert await relay.once()  # catch-up scan prompted by the reconnect wake
        assert [event.transaction_id for event in delivered] == [tx.id]
    finally:
        allow_reconnect.set(); stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert all(connection.is_closed() for connection in connections)


@pytest.mark.asyncio
async def test_t05_relay_polling_without_notify(event_db, monkeypatch):
    from app.payment_outbox import OutboxRelay
    repo, _, uid = event_db
    published = asyncio.Event()
    stop = asyncio.Event()
    class Stream:
        async def publish(self, event): published.set(); return '1-0'
    relay = OutboxRelay(repo, {'formation': Stream()}, poll_seconds=0.02)
    async def lost_notifications(*args): await stop.wait()
    monkeypatch.setattr(relay, 'listen', lost_notifications)
    task = asyncio.create_task(relay.run(os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], stop))
    try:
        await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        await asyncio.wait_for(published.wait(), timeout=1)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_t05_notify_after_commit_wakes_dedicated_listener(event_db):
    from app.payment_outbox import OutboxRelay
    repo, _, uid = event_db
    wake, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(OutboxRelay(repo, {}, poll_seconds=0.02).listen(
        os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL'], wake, stop))
    try:
        await asyncio.wait_for(wake.wait(), timeout=2)  # subscribed / catch-up
        wake.clear()
        await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
        await asyncio.wait_for(wake.wait(), timeout=1)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_t08_t10_stale_owner_cannot_write_business_rows_or_repeat_unknown_write(event_db):
    from app.models import PaymentJob, PaymentExecutionLease
    from app.payment_execution import current_execution, PaymentExecution, PaymentLeaseLost, PaymentWriteUnknown
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob))
    old = await repo.claim(job.id, 'old', 30)
    assert await repo.begin_write(old, 'marker', 'crm.item.add') is None
    async with sessions.begin() as s:
        (await s.get(PaymentExecutionLease, tx.id)).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    new = await repo.claim(job.id, 'new', 30)
    token = current_execution.set(PaymentExecution(repo, old))
    try:
        with pytest.raises(PaymentLeaseLost): await PaymentRepository(sessions).update(tx.id, status='failed')
        with pytest.raises(PaymentLeaseLost): await repo.confirm_write(old, 'marker', '123')
    finally:
        current_execution.reset(token)
    with pytest.raises(PaymentWriteUnknown): await repo.begin_write(new, 'marker', 'crm.item.add')


@pytest.mark.asyncio
async def test_t09_confirmed_write_result_is_replayed_without_http(event_db):
    import httpx
    from app.bitrix24_client import Bitrix24Client
    from app.config import Settings
    from app.models import PaymentJob
    from app.payment_execution import current_execution, PaymentExecution
    repo, sessions, uid = event_db
    await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob))
    claim = await repo.claim(job.id, 'test', 30)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'result': {'item': {'id': 123, 'private': 'not stored'}}})
    token = current_execution.set(PaymentExecution(repo, claim))
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = Bitrix24Client(Settings(_env_file=None, bx24_webhook='https://test.example/rest/1/secret'), http)
            assert await client.create_invoice({'xmlId': str(job.transaction_id)}) == 123
            assert await client.create_invoice({'xmlId': str(job.transaction_id)}) == 123
        assert len(calls) == 1
    finally:
        current_execution.reset(token)


@pytest.mark.asyncio
async def test_t06_t15_executor_parallel_payments_and_duplicate_event(event_db):
    from types import SimpleNamespace
    from app.config import Settings
    from app.models import PaymentJob
    from app.payment_job_executor import PaymentJobExecutor
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    for _ in range(2): await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s: jobs = list(await s.scalars(select(PaymentJob)))
    payments = PaymentRepository(sessions)
    calls, active = [], set()
    overlapped = False
    class Service:
        async def process(self, tid):
            nonlocal overlapped
            assert tid not in active
            active.add(tid); calls.append(tid)
            overlapped = overlapped or len(active) == 2
            await asyncio.sleep(0.1)
            active.remove(tid)
            return await payments.get(tid)
    services = SimpleNamespace(payment_repository=payments, payment_service=Service())
    settings = Settings(_env_file=None)
    consumers = [PaymentJobExecutor(services, repo, settings, str(i)) for i in range(3)]
    await asyncio.gather(consumers[0].execute(jobs[0].id), consumers[1].execute(jobs[0].id), consumers[2].execute(jobs[1].id))
    assert len(calls) == 2 and overlapped
    assert await consumers[1].execute(jobs[0].id)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_t24_global_limiter_counts_all_processes_and_cooldown(event_db):
    from app.config import Settings
    from app.payment_limiter import PaymentRequestLimiter
    repo, _, _ = event_db
    settings = Settings(_env_file=None, payment_bitrix_requests_per_second=20, payment_bitrix_cooldown_seconds=0.1)
    limiters = [PaymentRequestLimiter(repo, settings) for _ in range(3)]
    times = []
    async def take(limiter):
        await limiter.acquire()
        times.append(asyncio.get_running_loop().time())
    await asyncio.gather(*(take(l) for l in limiters))
    times.sort()
    assert all(b - a >= 0.04 for a, b in zip(times, times[1:]))
    start = asyncio.get_running_loop().time()
    await limiters[0].limited()
    await limiters[1].acquire()
    assert asyncio.get_running_loop().time() - start >= 0.09


@pytest.mark.asyncio
async def test_t20_selection_and_cancel_commands_are_durable_and_owner_checked(event_db):
    from app.errors import PaymentNotFoundError
    from app.models import PaymentJob
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    await PaymentRepository(sessions).update(tx.id, status='resolving_client')
    await PaymentRepository(sessions).update(tx.id, status='client_selection_required',
        candidate_snapshot=[{'entity_type': 'lead', 'entity_id': 10, 'display_name': 'test'}])
    result = await repo.queue_command(tx.id, uid, 'select', selection=('lead', 10))
    assert result.status == 'resolving_client'
    assert result.candidate_snapshot
    async with sessions() as s:
        jobs = list(await s.scalars(select(PaymentJob).order_by(PaymentJob.generation)))
        assert jobs[-1].details['selection'] == ['lead', 10]
    with pytest.raises(PaymentNotFoundError): await repo.queue_command(tx.id, uuid4(), 'cancel')
    await repo.queue_command(tx.id, uid, 'cancel')
    await repo.queue_command(tx.id, uid, 'cancel')
    async with sessions() as s:
        jobs = list(await s.scalars(select(PaymentJob)))
        assert sum(j.details.get('action') == 'cancel' for j in jobs) == 1


@pytest.mark.asyncio
async def test_t19_unknown_callback_is_saved_and_known_callback_wakes_poll(event_db):
    from app.models import PaymentCallbackInbox, PaymentJob
    repo, sessions, uid = event_db
    await repo.receive_callback(123)
    await repo.receive_callback(123)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentCallbackInbox)) == 1
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC), payment_id=456))
    await repo.receive_callback(456)
    await repo.receive_callback(456)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.kind == 'reconciliation')) == 1


@pytest.mark.asyncio
async def test_t13_t15_fallback_runs_without_any_redis_client(event_db):
    from types import SimpleNamespace
    from app.config import Settings
    from app.payment_job_executor import PaymentJobExecutor
    from app.payment_recovery import PaymentRecovery
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    payments = PaymentRepository(sessions)
    calls = []
    class Service:
        async def process(self, tid): calls.append(tid); return await payments.get(tid)
    services = SimpleNamespace(payment_repository=payments, payment_service=Service())
    executor = PaymentJobExecutor(services, repo, Settings(_env_file=None), 'fallback')
    recovery = PaymentRecovery(repo, executor, 'formation')
    assert await recovery.once() == 1
    assert calls == [tx.id]
    assert await recovery.once() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('paid_audit_done', [False, True])
async def test_paid_backfill_finishes_formation_then_only_missing_paid_audit(event_db, paid_audit_done):
    from types import SimpleNamespace
    from app.config import Settings
    from app.models import PaymentJob
    from app.payments import PaymentService
    from app.payment_job_executor import PaymentJobExecutor
    from app.repositories import PaymentRepository
    from test_payment_service import FakeBitrix, FakeResolver
    repo, sessions, uid = event_db
    payments = PaymentRepository(sessions)
    values = transaction_values(uid, now=datetime.now(UTC))
    values.update(status='paid', current_step='paid', bitrix_contact_id=5, bitrix_invoice_id=10,
        bitrix_payment_id=12, paid_timeline_created=paid_audit_done, paid_activity_created=paid_audit_done)
    tx, _ = await payments.create_or_get(idempotency_key=uuid4(), values=values)
    assert await repo.backfill() == 1
    assert await repo.backfill() == 0
    async with sessions() as s:
        job = await s.scalar(select(PaymentJob).where(PaymentJob.transaction_id == tx.id))
    assert job.kind == 'formation'
    bitrix = FakeBitrix()
    async def missing_activity(marker): return None
    bitrix.activity_id_by_marker = missing_activity
    settings = Settings(_env_file=None)
    service = PaymentService(settings, payments, SimpleNamespace(), FakeResolver(), bitrix)
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments, payment_service=service), repo, settings, 'paid-backfill')
    assert await executor.execute(job.id)
    result = await payments.get(tx.id)
    assert result.status == 'paid'
    assert result.formation_timeline_created and result.formation_activity_created
    async with sessions() as s:
        jobs = list(await s.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id, PaymentJob.state == 'ready')))
    assert [job.kind for job in jobs] == ([] if paid_audit_done else ['reconciliation'])


@pytest.mark.asyncio
async def test_t14_t28_recovery_and_backfill_are_idempotent(event_db):
    from app.models import PaymentJob, PaymentOutbox
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await PaymentRepository(sessions).create_or_get(idempotency_key=uuid4(), values=transaction_values(uid, now=datetime.now(UTC)))
    assert await repo.backfill() == 1
    assert await repo.backfill() == 0
    row = await repo.claim_outbox(30)
    await repo.published(row.event_id, row.publisher_token, '1-0')
    async with sessions.begin() as s:
        (await s.get(PaymentOutbox, row.event_id)).last_published_at = datetime.now(UTC) - timedelta(seconds=31)
    assert await repo.recover_publications() == 1
    assert await repo.claim_outbox(30) is not None
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentJob)) == 1


@pytest.mark.asyncio
async def test_t28_mode_interlock_blocks_live_switch_and_mismatched_producer(event_db):
    from app.payment_runtime_registry import PaymentRuntimeRegistry
    from app.payment_event_repository import PaymentEventRepository
    from app.errors import ApiError
    _, sessions, uid = event_db
    registry = PaymentRuntimeRegistry(sessions, 'legacy')
    await registry.register('old-api', 'api')
    with pytest.raises(RuntimeError): await registry.set_mode('events')
    await registry.unregister('old-api')
    await registry.set_mode('events')
    with pytest.raises(ApiError): await registry.register('old-worker', 'legacy')
    old = PaymentEventRepository(sessions, expected_mode='legacy')
    with pytest.raises(ApiError): await old.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    new = PaymentEventRepository(sessions, expected_mode='events')
    tx, created = await new.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    assert created


@pytest.mark.asyncio
async def test_t09_unknown_invoice_write_recovers_by_xml_id_without_new_write(event_db):
    import httpx
    from types import SimpleNamespace
    from app.bitrix24_client import Bitrix24Client
    from app.config import Settings
    from app.models import PaymentJob, PaymentExecutionLease
    from app.payment_execution import current_execution, PaymentExecution
    from app.payment_job_executor import PaymentJobExecutor
    from app.repositories import PaymentRepository
    repo, sessions, uid = event_db
    tx, _ = await repo.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    async with sessions() as s: job = await s.scalar(select(PaymentJob))
    old = await repo.claim(job.id, 'old', 30)
    writes = []
    def handler(request):
        if request.url.path.endswith('crm.item.add.json'):
            writes.append(1)
            raise httpx.ReadTimeout('response lost after remote commit')
        return httpx.Response(200, json={'result': {'items': [{'id': 123}]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        settings = Settings(_env_file=None, bx24_webhook='https://test.example/rest/1/secret')
        bitrix = Bitrix24Client(settings, http)
        token = current_execution.set(PaymentExecution(repo, old))
        try:
            from app.errors import Bitrix24Error
            with pytest.raises(Bitrix24Error): await bitrix.create_invoice({'xmlId': str(tx.id)})
        finally:
            current_execution.reset(token)
        async with sessions.begin() as s:
            (await s.get(PaymentExecutionLease, tx.id)).lease_until = datetime.now(UTC) - timedelta(seconds=1)
        payments = PaymentRepository(sessions)
        class Service:
            async def process(self, tid):
                # Same write is answered from the recovered result, not repeated.
                assert await bitrix.create_invoice({'xmlId': str(tid)}) == 123
                return await payments.get(tid)
        services = SimpleNamespace(payment_repository=payments, payment_service=Service(), bitrix=bitrix)
        assert await PaymentJobExecutor(services, repo, settings, 'replacement').execute(job.id)
    assert len(writes) == 1
    assert not await repo.unknown_writes(tx.id)
