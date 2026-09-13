"""Real API admission and PostgreSQL outbox; no external integration writes."""
import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import app, settings
from app.models import PaymentTransaction, PaymentJob, PaymentOutbox
from app.payments import PaymentService
from app.payment_event_repository import PaymentEventRepository
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payments_api import FakeSessionStore, session, payment_payload
from test_payment_database_outage import DatabaseGate

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_at', ['before_request', 'before_commit'])
async def test_api_database_outage_rejects_and_same_key_can_be_retried(event_db, monkeypatch, failure_at):
    _, sessions, uid = event_db
    async with sessions() as db:
        schema = await db.scalar(text('SELECT current_schema()'))
    url = sessions.kw['bind'].url
    gate = DatabaseGate(url.host, url.port or 5432)
    server = await asyncio.start_server(gate.accept, '127.0.0.1', 0)
    engine = create_async_engine(url.set(host='127.0.0.1', port=server.sockets[0].getsockname()[1]),
        connect_args={'server_settings': {'search_path': schema}, 'timeout': 2}, pool_pre_ping=True)
    queue = PaymentEventRepository(async_sessionmaker(engine, expire_on_commit=False))
    authenticated = session().model_copy(update={'internal_user_id': uid})
    class Catalog:
        async def get(self, product_id):
            return SimpleNamespace(product_id=product_id, title='Test', default_amount=Decimal('1500'), currency='RUB')
    service = PaymentService(Settings(_env_file=None, payment_telemetry_enabled=False),
        PaymentRepository(queue.sessions, events=queue), Catalog(), None, None)
    monkeypatch.setattr(app.state, 'payment_service', service, raising=False)
    monkeypatch.setattr(app.state, 'session_store', FakeSessionStore(authenticated), raising=False)
    original_enqueue = queue.enqueue_in_session
    inject = True
    async def cut_before_commit(*args, **kwargs):
        result = await original_enqueue(*args, **kwargs)
        if inject:
            await gate.cut()
        return result
    if failure_at == 'before_commit':
        monkeypatch.setattr(queue, 'enqueue_in_session', cut_before_commit)
    try:
        if failure_at == 'before_request':
            await gate.cut()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url='http://test', cookies={settings.session_cookie_name: 'sid'},
                headers={'X-CSRF-Token': 'csrf-test'}) as client:
            payload = payment_payload()
            failed = await asyncio.wait_for(client.post('/api/payments', json=payload), 5)
            assert failed.status_code == 500
            async with sessions() as db:
                for model in (PaymentTransaction, PaymentJob, PaymentOutbox):
                    assert await db.scalar(select(func.count()).select_from(model)) == 0
            inject = False
            gate.enabled = True
            await engine.dispose()
            accepted = await client.post('/api/payments', json=payload)
            repeated = await client.post('/api/payments', json=payload)
            assert accepted.status_code == repeated.status_code == 202
            assert accepted.json()['id'] == repeated.json()['id']
            async with sessions() as db:
                for model in (PaymentTransaction, PaymentJob, PaymentOutbox):
                    assert await db.scalar(select(func.count()).select_from(model)) == 1
    finally:
        await engine.dispose()
        await gate.cut()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_outbox', [False, True])
async def test_api_admission_atomic_and_concurrent_idempotent(event_db, monkeypatch, fail_outbox):
    queue, sessions, uid = event_db
    authenticated = session().model_copy(update={'internal_user_id': uid})

    class Catalog:
        async def get(self, product_id):
            return SimpleNamespace(product_id=product_id, title='Test', default_amount=Decimal('1500'), currency='RUB')

    service = PaymentService(Settings(_env_file=None, payment_telemetry_enabled=False),
        PaymentRepository(sessions, events=queue), Catalog(), None, None)
    monkeypatch.setattr(app.state, 'payment_service', service, raising=False)
    monkeypatch.setattr(app.state, 'session_store', FakeSessionStore(authenticated), raising=False)
    if fail_outbox:
        original = queue.enqueue_in_session
        async def fail_after_insert(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('test transaction rollback')
        monkeypatch.setattr(queue, 'enqueue_in_session', fail_after_insert)

    payload = payment_payload()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url='http://test', cookies={settings.session_cookie_name: 'sid'},
            headers={'X-CSRF-Token': 'csrf-test'}) as client:
        # Two independent HTTP requests use real service and separate DB sessions.
        responses = await asyncio.gather(*(client.post('/api/payments', json=payload) for _ in range(2)))
        assert [r.status_code for r in responses] == ([500, 500] if fail_outbox else [202, 202])
        if not fail_outbox:
            assert responses[0].json()['id'] == responses[1].json()['id']
            assert (await client.get('/api/payments/' + responses[0].json()['id'])).status_code == 200

    async with sessions() as db:
        for model in (PaymentTransaction, PaymentJob, PaymentOutbox):
            assert await db.scalar(select(func.count()).select_from(model)) == (0 if fail_outbox else 1)
        if not fail_outbox:
            payment = await db.scalar(select(PaymentTransaction))
            assert payment.user_id == uid
            assert payment.employee_external_id == str(authenticated.user.id)
