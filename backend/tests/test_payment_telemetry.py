import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.payment_telemetry import Trace, current_trace, current_parent, operation, span
from app.routers.payment_timings import BrowserTiming, summary
from test_payment_service import FakeBitrix, FakeRepository, FakeResolver, make_transaction
from app.payments import PaymentService


@pytest.mark.asyncio
async def test_spans_nested_error_safe_and_context_restored():
    trace = Trace(); trace.transaction_id = uuid4()
    token = current_trace.set(trace)
    try:
        with span("outer"):
            with pytest.raises(RuntimeError), span("inner"):
                raise RuntimeError("secret must not be stored")
        inner, outer = trace.rows
        assert inner["parent_id"] == outer["id"]
        assert inner["outcome"] == "error"
        assert inner["duration_ms"] >= 0
        assert "secret" not in str(trace.rows)
        assert current_parent.get() is None
        await trace.save(None)  # Broken persistence must not raise.
    finally:
        current_trace.reset(token)


@pytest.mark.asyncio
async def test_worker_stages_and_link_milestone_before_timeline():
    transaction = make_transaction()
    class Repo(FakeRepository):
        async def save_spans(self, transaction_id, run_id, rows): self.spans = rows
    repo = Repo(transaction)
    service = PaymentService(Settings(_env_file=None, payment_telemetry_enabled=True), repo, SimpleNamespace(), FakeResolver(), FakeBitrix())
    await service.process(transaction.id)
    names = [r["name"] for r in repo.spans]
    for name in ["initial_queue", "resolve_client", "invoice", "product", "payment_document", "payment_product", "public_link", "timeline", "send", "activity"]:
        assert name in names
    assert names.index("link_ready") < names.index("timeline")
    assert current_trace.get() is None

    # Persistence failure must not alter the successful business result.
    class BrokenRepo(FakeRepository):
        async def save_spans(self, *args): raise RuntimeError("database failure")
    other = make_transaction()
    result = await PaymentService(Settings(_env_file=None, payment_telemetry_enabled=True), BrokenRepo(other), SimpleNamespace(), FakeResolver(), FakeBitrix()).process(other.id)
    assert result.payment_url == "https://pay/full"
    assert result.status == "send_failed"


@pytest.mark.asyncio
async def test_concurrent_operations_have_isolated_traces():
    saved = []
    class Repo:
        async def save_spans(self, transaction_id, run_id, rows): saved.append((transaction_id, run_id, rows))
    class Service:
        _repository = Repo()
        _settings = Settings(_env_file=None, payment_telemetry_enabled=True)
        @operation("worker")
        async def run(self, transaction_id):
            with span("work"):
                await asyncio.sleep(0)
    ids = [uuid4(), uuid4()]
    await asyncio.gather(*(Service().run(i) for i in ids))
    assert {s[0] for s in saved} == set(ids)
    assert saved[0][1] != saved[1][1]
    assert all(len(s[2]) == 2 for s in saved)


@pytest.mark.asyncio
async def test_rest_retry_attempts_record_status_without_payload():
    count = 0
    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(429 if count == 1 else 200, json={"result": "secret-response"})
    trace = Trace(); token = current_trace.set(trace)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = Bitrix24Client(Settings(_env_file=None, bx24_webhook="https://example.test/rest/1/secret"), http)
            await client.call("crm.contact.get", {"id": 1})
        assert [r["details"]["attempt"] for r in trace.rows if r["kind"] == "rest"] == [1, 2]
        assert trace.rows[0]["outcome"] == "error"
        assert "secret" not in str(trace.rows)
    finally:
        current_trace.reset(token)


@pytest.mark.parametrize("payload", [{"qr_ms": -1}, {"qr_ms": float('inf')}, {"qr_ms": 1, "accepted_ms": 2}, {"qr_ms": 1, "phone": "secret"}])
def test_browser_payload_rejects_invalid_and_extra_fields(payload):
    with pytest.raises(ValidationError): BrowserTiming(**payload)


def test_missing_metrics_are_not_zero():
    result = summary(make_transaction(), [])
    assert result["qr_ms"] is None
    assert result["queue_ms"] is None
    assert result["link_ms"] is None
    assert not result["measured"]


@pytest.mark.asyncio
async def test_telemetry_off_by_default_does_not_create_or_save_traces(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.payment_telemetry_enabled is False
    def forbidden_trace(): raise AssertionError("Trace must not be created")
    monkeypatch.setattr("app.payment_telemetry.Trace", forbidden_trace)
    transaction = make_transaction()
    class Repo(FakeRepository):
        async def save_spans(self, *args): raise AssertionError("Must not write telemetry")
    service = PaymentService(settings, Repo(transaction), SimpleNamespace(), FakeResolver(), FakeBitrix())
    result = await service.process(transaction.id)
    assert result.payment_url == "https://pay/full"
    assert current_trace.get() is None


def test_telemetry_environment_switch(monkeypatch):
    monkeypatch.setenv("PAYMENT_TELEMETRY_ENABLED", "true")
    assert Settings(_env_file=None).payment_telemetry_enabled is True
    monkeypatch.setenv("PAYMENT_TELEMETRY_ENABLED", "false")
    assert Settings(_env_file=None).payment_telemetry_enabled is False
