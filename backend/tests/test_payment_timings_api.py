from uuid import uuid4

import httpx
import pytest

from app.errors import PaymentNotFoundError
from app.main import app, settings
from test_payments_api import session, FakeSessionStore


@pytest.mark.asyncio
async def test_job_retry_requires_admin_and_csrf_and_returns_acceptance(monkeypatch):
    from types import SimpleNamespace
    user_session = session()
    calls = []
    new_id = uuid4()
    class Queue:
        async def retry_job(self, job_id, admin_id):
            calls.append((job_id, admin_id))
            return new_id
    monkeypatch.setattr(app.state, 'payment_repository', SimpleNamespace(event_queue=Queue()), raising=False)
    monkeypatch.setattr(app.state, 'session_store', FakeSessionStore(user_session), raising=False)
    job_id = uuid4()
    url = f'/api/admin/payment-jobs/{job_id}/retry'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(url)).status_code == 401
        client.cookies.set(settings.session_cookie_name, 'sid')
        headers = {'X-CSRF-Token': 'csrf-test'}
        assert (await client.post(url, headers=headers)).status_code == 403
        user_session.user.status = 'admin'
        assert (await client.post(url)).status_code == 403
        assert calls == []
        response = await client.post(url, headers=headers)
        assert response.status_code == 202
        assert response.json() == {'job_id': str(new_id)}
        assert len(calls) == 1 and calls[0][0] == job_id


@pytest.mark.asyncio
async def test_admin_job_diagnostics_work_with_telemetry_disabled(monkeypatch):
    from test_payment_service import make_transaction
    monkeypatch.setattr(settings, 'payment_telemetry_enabled', False)
    tx = make_transaction()
    user_session = session(); user_session.user.status = 'admin'
    job = {'id': uuid4(), 'transaction_id': tx.id, 'state': 'needs_reconciliation', 'error': 'EXTERNAL_WRITE_UNKNOWN'}
    class Repo:
        async def get(self, tid): return tx if tid == tx.id else None
        async def timing_detail(self, tid): return []
        async def job_diagnostics(self, tids):
            assert tids == [tx.id]
            return [job]
        async def timing_list(self, **kwargs): return [tx], [], 1, {'samples': 0, 'median_ms': None, 'p95_ms': None, 'failed': 0}
    monkeypatch.setattr(app.state, 'payment_repository', Repo(), raising=False)
    monkeypatch.setattr(app.state, 'session_store', FakeSessionStore(user_session), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        client.cookies.set(settings.session_cookie_name, 'sid')
        response = await client.get('/api/admin/payment-timings')
        assert response.status_code == 200
        assert response.json()['items'][0]['job_issues'][0]['id'] == str(job['id'])
        response = await client.get(f'/api/admin/payment-timings/{tx.id}')
        assert response.status_code == 200
        assert response.json()['spans'] == []
        assert response.json()['jobs'][0]['error'] == 'EXTERNAL_WRITE_UNKNOWN'


@pytest.mark.asyncio
async def test_queue_health_is_admin_only(monkeypatch):
    from types import SimpleNamespace
    user_session = session()
    calls = []
    async def snapshot(sessions, config):
        calls.append(True)
        return {'alerts': ['OUTBOX_PUBLICATION_STALLED']}
    monkeypatch.setattr('app.payment_health.queue_health', snapshot)
    monkeypatch.setattr(app.state, 'payment_repository', SimpleNamespace(event_queue=SimpleNamespace(sessions=None)), raising=False)
    monkeypatch.setattr(app.state, 'session_store', FakeSessionStore(user_session), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/admin/payment-jobs/health')).status_code == 401
        client.cookies.set(settings.session_cookie_name, 'sid')
        assert (await client.get('/api/admin/payment-jobs/health')).status_code == 403
        assert not calls
        user_session.user.status = 'admin'
        response = await client.get('/api/admin/payment-jobs/health')
        assert response.status_code == 200
        assert response.json()['alerts'] == ['OUTBOX_PUBLICATION_STALLED']
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_timing_admin_access_and_browser_ownership(monkeypatch):
    monkeypatch.setattr(settings, "payment_telemetry_enabled", True)
    transaction_id = uuid4()
    saved = []
    class Repo:
        async def timing_list(self, **kwargs): return [], [], 0, {"samples": 0, "median_ms": None, "p95_ms": None, "failed": 0}
        async def save_browser_timing(self, *args): saved.append(args)
    class Service:
        async def get(self, actor, payment_id):
            if payment_id != transaction_id: raise PaymentNotFoundError()
    user_session = session()
    monkeypatch.setattr(app.state, "payment_repository", Repo(), raising=False)
    monkeypatch.setattr(app.state, "payment_service", Service(), raising=False)
    monkeypatch.setattr(app.state, "session_store", FakeSessionStore(user_session), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get('/api/admin/payment-timings')).status_code == 401
        client.cookies.set(settings.session_cookie_name, 'sid')
        assert (await client.get('/api/auth/session')).json()['payment_telemetry_enabled'] is True
        assert (await client.get('/api/admin/payment-timings')).status_code == 403
        assert (await client.get(f'/api/admin/payment-timings/{transaction_id}')).status_code == 403
        user_session.user.status = "admin"
        assert (await client.get('/api/admin/payment-timings')).status_code == 200
        assert (await client.get('/api/admin/payment-timings?page=0')).status_code == 422
        assert (await client.get('/api/admin/payment-timings?date_from=bad')).status_code == 422
        url = f'/api/payments/{transaction_id}/timing'
        assert (await client.post(url, json={"qr_ms": 5})).status_code == 403
        headers = {"X-CSRF-Token": "csrf-test"}
        assert (await client.post(f'/api/payments/{uuid4()}/timing', json={"qr_ms": 5}, headers=headers)).status_code == 404
        assert (await client.post(url, json={"qr_ms": 5}, headers=headers)).status_code == 204
        assert len(saved) == 1
        assert saved[0][1][0]["duration_ms"] == 5
        monkeypatch.setattr(settings, "payment_telemetry_enabled", False)
        assert (await client.get('/api/auth/session')).json()['payment_telemetry_enabled'] is False
        assert (await client.post(url, json={"qr_ms": 10}, headers=headers)).status_code == 204
        assert len(saved) == 1
        assert (await client.get('/api/admin/payment-timings')).status_code == 200
