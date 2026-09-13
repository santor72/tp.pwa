"""T21: old formation events stay harmless; late authoritative paid wins."""
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import PaymentJob
from app.payment_job_executor import PaymentJobExecutor
from app.payment_status import PaymentStatusHandler
from app.payments import PaymentService
from app.repositories import PaymentRepository
from test_payment_events_repository import event_db
from test_payment_repository_postgres import transaction_values
from test_payment_status import Bitrix, Repository, transaction

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['canceled', 'expired', 'paid'])
async def test_old_formation_event_does_not_resume_terminal_payment(event_db, status):
    queue, sessions, uid = event_db
    tx, _ = await queue.create_payment(uuid4(), transaction_values(uid, now=datetime.now(UTC)))
    payments = PaymentRepository(sessions)
    terminal = dict(status=status, current_step=status, next_attempt_at=None)
    if status == 'paid':
        # Formation audit was already durable, so a stale delivery must be a no-op.
        terminal.update(formation_timeline_created=True, formation_activity_created=True,
            paid_timeline_created=True, paid_activity_created=True)
    await payments.update(tx.id, **terminal)
    async with sessions() as db:
        job = await db.scalar(select(PaymentJob))
    class ForbiddenBitrix:
        def __getattr__(self, name):
            async def forbidden(*args, **kwargs): raise AssertionError(f'old event called {name}')
            return forbidden
    service = PaymentService(Settings(_env_file=None), payments, None, None, ForbiddenBitrix())
    executor = PaymentJobExecutor(SimpleNamespace(payment_repository=payments, payment_service=service),
        queue, Settings(_env_file=None, payment_telemetry_enabled=False), 'terminal-event')
    assert await executor.execute(job.id)
    result = await payments.get(tx.id)
    assert result.status == status and result.current_step == status
    assert (await queue.get_job(job.id)).state == 'completed'


@pytest.mark.asyncio
async def test_late_paid_confirmation_overrides_canceled_state_without_recreating_payment():
    tx = transaction()
    tx.status = 'canceled'; tx.current_step = 'canceled'
    repo = Repository(tx); bitrix = Bitrix('Y'); bitrix.marker_exists = True
    result = await PaymentStatusHandler(Settings(_env_file=None), repo, bitrix).handle(12)
    assert result.status == 'paid' and result.current_step == 'paid'
    assert result.paid_timeline_created and result.paid_activity_created
    assert bitrix.comments == [] and bitrix.activities == []
    assert any(event[1] == 'payment.paid' for event in repo.events)
