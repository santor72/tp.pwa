from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.payment_status import PaymentStatusHandler


class Repository:
    def __init__(self, transaction): self.transaction = transaction; self.events = []
    async def get_by_payment_id(self, payment_id): return self.transaction if payment_id == 12 else None
    async def update(self, transaction_id, **values):
        for key, value in values.items(): setattr(self.transaction, key, value)
        return self.transaction
    async def add_event(self, *args): self.events.append(args)


class Bitrix:
    def __init__(self, paid): self.paid = paid; self.comments = []; self.activities = []; self.marker_exists = False; self.completed = []
    async def get_payment(self, payment_id): return {"paid": self.paid, "accountNumber": "PAY-1", "paySystemId": 23, "paySystemName": "СБП"}
    async def timeline_has_marker(self, *args): return self.marker_exists
    async def timeline_comment(self, *args): self.comments.append(args)
    async def activity_has_marker(self, *args): return self.marker_exists
    async def add_activity(self, fields): self.activities.append(fields); return 1
    async def activity_id_by_marker(self, *args): return 99
    async def complete_activity(self, activity_id): self.completed.append(activity_id)


def transaction():
    return SimpleNamespace(
        id=uuid4(), status="sent", bitrix_payment_id=12, bitrix_contact_id=5, bitrix_lead_id=6,
        paid_timeline_created=False, paid_activity_created=False, actual_amount=Decimal("10.00"),
        currency="RUB", product_title="Услуга", expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_t22_paid_is_saved_even_when_followup_activity_fails():
    tx = transaction(); repo = Repository(tx); bitrix = Bitrix('Y')
    async def failed_activity(fields): raise RuntimeError('CRM followup unavailable')
    bitrix.add_activity = failed_activity
    handler = PaymentStatusHandler(Settings(_env_file=None), repo, bitrix)
    with pytest.raises(RuntimeError): await handler.handle(12)
    assert tx.status == 'paid'
    assert tx.paid_timeline_created is True
    assert tx.paid_activity_created is False


@pytest.mark.asyncio
async def test_status_handler_trusts_only_confirmed_paid_flag_and_is_idempotent() -> None:
    tx = transaction(); repository = Repository(tx); bitrix = Bitrix("N"); handler = PaymentStatusHandler(Settings(), repository, bitrix)
    await handler.handle(12)
    assert tx.status == "sent" and not bitrix.comments
    bitrix.paid = "Y"
    await handler.handle(12)
    assert tx.status == "paid" and tx.paid_at.tzinfo is not None
    assert len(bitrix.comments) == 2 and len(bitrix.activities) == 1
    assert bitrix.completed == [99]
    assert (tx.bitrix_payment_account_number, tx.bitrix_pay_system_id, tx.bitrix_pay_system_name) == ("PAY-1", 23, "СБП")
    await handler.handle(12)
    assert len(bitrix.comments) == 2 and len(bitrix.activities) == 1


@pytest.mark.asyncio
async def test_status_handler_recovers_after_remote_write_before_local_flag() -> None:
    tx = transaction(); repository = Repository(tx); bitrix = Bitrix("Y"); bitrix.marker_exists = True
    result = await PaymentStatusHandler(Settings(), repository, bitrix).handle(12)
    assert result.status == "paid"
    assert result.paid_timeline_created and result.paid_activity_created
    assert bitrix.comments == [] and bitrix.activities == []


@pytest.mark.asyncio
async def test_status_handler_expires_only_after_bitrix_confirms_payment_is_unpaid() -> None:
    tx = transaction(); tx.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    repository = Repository(tx); bitrix = Bitrix("N")
    result = await PaymentStatusHandler(Settings(), repository, bitrix).handle(12)
    assert result.status == "expired"
    assert repository.events[-1][1] == "payment.expired"

    tx = transaction(); tx.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    repository = Repository(tx); bitrix = Bitrix("Y")
    result = await PaymentStatusHandler(Settings(), repository, bitrix).handle(12)
    assert result.status == "paid"
