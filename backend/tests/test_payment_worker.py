from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import PaymentStateError
from app.payment_worker import process_once


class Repository:
    def __init__(self, rows): self.rows = rows; self.expired = []; self.claimed = []
    async def expire_due(self, now): self.expired.append(now); return 0
    async def claim_batch(self, statuses, now, limit): self.claimed.append((statuses, now, limit)); return list(self.rows)
    async def get(self, transaction_id): return rows_by_id[transaction_id]


class PaymentService:
    def __init__(self): self.processed = []; self.failed = []; self.raise_for = None
    async def process(self, transaction_id):
        self.processed.append(transaction_id)
        if transaction_id == self.raise_for: raise RuntimeError("temporary")
    async def mark_failure(self, transaction_id, exc): self.failed.append((transaction_id, type(exc).__name__))


class PaymentStatus:
    def __init__(self): self.handled = []
    async def handle(self, payment_id): self.handled.append(payment_id)


rows_by_id = {}


@pytest.mark.asyncio
async def test_worker_claims_batch_polls_ready_payments_and_retries_other_work() -> None:
    poll_id, work_id, failed_id = uuid4(), uuid4(), uuid4()
    global rows_by_id
    rows_by_id = {
        poll_id: SimpleNamespace(status="send_queued", bitrix_payment_id=12, formation_timeline_created=True, formation_activity_created=True),
        work_id: SimpleNamespace(status="invoice_created", bitrix_payment_id=None, formation_timeline_created=False, formation_activity_created=False),
        failed_id: SimpleNamespace(status="draft", bitrix_payment_id=None, formation_timeline_created=False, formation_activity_created=False),
    }
    repository = Repository([poll_id, work_id, failed_id]); payment_service = PaymentService(); payment_service.raise_for = failed_id
    status = PaymentStatus()
    services = SimpleNamespace(payment_repository=repository, payment_service=payment_service, payment_status=status)
    now = datetime.now(UTC)

    assert await process_once(services, Settings(bx24_worker_batch_size=3), now) == 3
    assert status.handled == [12]
    assert payment_service.processed == [work_id, failed_id]
    assert payment_service.failed == [(failed_id, "RuntimeError")]
    assert repository.expired == [now]
    assert repository.claimed[0][2] == 3


@pytest.mark.asyncio
async def test_worker_does_not_mark_superseded_state_as_external_failure() -> None:
    transaction_id = uuid4()
    global rows_by_id
    rows_by_id = {transaction_id: SimpleNamespace(status="draft", bitrix_payment_id=None, formation_timeline_created=False, formation_activity_created=False)}
    repository = Repository([transaction_id])

    class SupersededService(PaymentService):
        async def process(self, _: object): raise PaymentStateError()

    service = SupersededService(); services = SimpleNamespace(payment_repository=repository, payment_service=service, payment_status=PaymentStatus())
    await process_once(services, Settings(), datetime.now(UTC))
    assert service.failed == []
