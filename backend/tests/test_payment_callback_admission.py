"""T19: webhook response follows durable commit, never inline CRM work."""
import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.main import app
from app.models import PaymentJob, PaymentOutbox
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('rollback', [False, True])
async def test_callback_waits_for_commit_and_deduplicates_without_inline_crm(event_db, monkeypatch, rollback):
    queue, sessions, uid = event_db
    await queue.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC), payment_id=456))
    monkeypatch.setattr(app.state, 'payment_repository', PaymentRepository(sessions, events=queue), raising=False)
    inline = AsyncMock(side_effect=AssertionError('callback must not execute CRM inline'))
    monkeypatch.setattr(app.state, 'payment_status', SimpleNamespace(handle=inline), raising=False)
    config = Settings(_env_file=None, bx24_payment_webhook_token='callback-test-secret')
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: config)
    inserted, release = asyncio.Event(), asyncio.Event()
    original = queue.enqueue_in_session
    async def pause_after_insert(*args, **kwargs):
        result = await original(*args, **kwargs)
        inserted.set()
        await release.wait()
        if rollback:
            raise RuntimeError('test callback rollback before commit')
        return result
    monkeypatch.setattr(queue, 'enqueue_in_session', pause_after_insert)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
        endpoint = '/api/webhooks/bitrix24/payments'
        payload = {'data': {'FIELDS': {'ID': 456}}}
        assert (await client.post(endpoint, json=payload)).status_code == 403
        assert not inserted.is_set()
        request = asyncio.create_task(client.post(endpoint, json=payload, headers={'x-bitrix-token': 'callback-test-secret'}))
        try:
            await asyncio.wait_for(inserted.wait(), 2)
            assert not request.done()
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.kind == 'reconciliation')) == 0
            release.set()
            response = await asyncio.wait_for(request, 2)
            assert response.status_code == (500 if rollback else 202)
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.kind == 'reconciliation')) == (0 if rollback else 1)
            monkeypatch.setattr(queue, 'enqueue_in_session', original)
            for _ in range(2):
                response = await client.post(endpoint, data={'data[FIELDS][ID]': '456', 'auth[application_token]': 'callback-test-secret'})
                assert response.status_code == 202
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(PaymentJob).where(PaymentJob.kind == 'reconciliation')) == 1
                assert await db.scalar(select(func.count()).select_from(PaymentOutbox)) == 2
            inline.assert_not_awaited()
        finally:
            release.set()
            if not request.done(): request.cancel()
            await asyncio.gather(request, return_exceptions=True)
