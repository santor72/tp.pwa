from uuid import uuid4

import httpx
import pytest

from app.errors import PaymentNotFoundError
from app.main import app, settings
from test_payments_api import session, FakeSessionStore


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
