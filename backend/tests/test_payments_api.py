from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.main import app, settings
from app.schemas import PaymentAcceptedResponse, SessionData, UserProfile


class FakeSessionStore:
    def __init__(self, session): self.session = session
    async def get(self, session_id): return self.session


class FakePaymentService:
    def __init__(self): self.created = []
    async def create(self, actor, employee_name, payload):
        self.created.append((actor, payload))
        return PaymentAcceptedResponse(id=payload.idempotency_key, status="draft"), True


class FakePaymentStatus:
    def __init__(self): self.ids = []
    async def handle(self, payment_id): self.ids.append(payment_id)


def session(permission: bool = True) -> SessionData:
    now = datetime.now(UTC)
    return SessionData(
        user=UserProfile(id=7, email="employee@example.test", first_name="Иван", status="active", user_permissions={"client": {"create": permission}}),
        internal_user_id=uuid4(), csrf_token="csrf-test", created_at=now, absolute_expires_at=now + timedelta(hours=1),
    )


def payment_payload() -> dict:
    return {
        "idempotency_key": str(uuid4()), "product_id": 123, "first_name": "Иван", "last_name": "Иванов",
        "phone": "+79991234567", "amount": "1500.00",
    }


@pytest.mark.asyncio
async def test_create_payment_requires_session_and_csrf() -> None:
    service = FakePaymentService(); app.state.payment_service = service; app.state.session_store = FakeSessionStore(session())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/payments", json=payment_payload())
        assert response.status_code == 401
        client.cookies.set(settings.session_cookie_name, "sid")
        response = await client.post("/api/payments", json=payment_payload())
        assert response.status_code == 403
        response = await client.post("/api/payments", headers={"X-CSRF-Token": "csrf-test", "Origin": "https://attacker.example"}, json=payment_payload())
        assert response.status_code == 403
        response = await client.post("/api/payments", headers={"X-CSRF-Token": "csrf-test"}, json=payment_payload())
        assert response.status_code == 202
    assert len(service.created) == 1


@pytest.mark.asyncio
async def test_create_payment_requires_explicit_capability() -> None:
    app.state.payment_service = FakePaymentService()
    app.state.session_store = FakeSessionStore(session(False))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies={settings.session_cookie_name: "sid"}) as client:
        response = await client.post("/api/payments", headers={"X-CSRF-Token": "csrf-test"}, json=payment_payload())
    assert response.status_code == 403


def test_old_domofon_write_routes_are_removed() -> None:
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    assert ("/api/domofon/connect", "POST") not in routes
    assert ("/api/domofon/create", "POST") not in routes
    assert ("/api/payments", "POST") in routes


@pytest.mark.asyncio
async def test_payment_webhook_requires_secret_and_accepts_bitrix_form() -> None:
    status_handler = FakePaymentStatus()
    app.state.payment_status = status_handler
    original = settings.bx24_payment_webhook_token
    settings.bx24_payment_webhook_token = type(original)("hook-secret")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/api/webhooks/bitrix24/payments", content="data%5BFIELDS%5D%5BID%5D=12")
            accepted = await client.post(
                "/api/webhooks/bitrix24/payments",
                content="data%5BFIELDS%5D%5BID%5D=12&auth%5Bapplication_token%5D=hook-secret",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        assert denied.status_code == 403
        assert accepted.status_code == 202
        assert status_handler.ids == [12]
    finally:
        settings.bx24_payment_webhook_token = original
