from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import Bitrix24Error
from app.errors import PaymentNotFoundError
from app.payment_client_resolver import ResolvedClient
from app.payments import PaymentService


class FakeRepository:
    def __init__(self, transaction) -> None:
        self.transaction = transaction
        self.events: list[tuple[str, dict]] = []
    async def get(self, transaction_id, user_id=None):
        if transaction_id != self.transaction.id or (user_id and user_id != self.transaction.user_id): return None
        return self.transaction
    async def update(self, transaction_id, **values):
        assert transaction_id == self.transaction.id
        for key, value in values.items(): setattr(self.transaction, key, value)
        self.transaction.updated_at = datetime.now(UTC)
        return self.transaction
    async def add_event(self, transaction_id, event_type, safe_payload=None, external_request_id=None):
        self.events.append((event_type, safe_payload or {}))


class FakeResolver:
    async def resolve(self, transaction): return ResolvedClient(contact_id=5, lead_id=6)


class FakeBitrix:
    def __init__(self) -> None: self.calls: list[tuple] = []; self.invoice_fields = {}
    async def find_invoice_by_xml_id(self, xml_id): self.calls.append(("find_invoice", xml_id)); return None
    async def create_invoice(self, fields): self.calls.append(("invoice", fields)); return 10
    async def find_product_row(self, invoice_id, product_id): return None
    async def add_product_row(self, invoice_id, fields): self.calls.append(("row", invoice_id, fields)); return 11
    async def find_payment(self, invoice_id): return None
    async def create_payment(self, invoice_id): self.calls.append(("payment", invoice_id)); return 12
    async def payment_product_linked(self, payment_id, row_id): return False
    async def add_payment_product(self, payment_id, row_id): self.calls.append(("payment_product", payment_id, row_id)); return 13
    async def payment_public_url(self, payment_id): self.calls.append(("url", payment_id)); return {"url": "https://pay/full", "short_url": "https://pay/s", "qr": "data:image/png;base64,AA=="}
    async def timeline_has_marker(self, entity_type, entity_id, marker): return False
    async def timeline_comment(self, entity_type, entity_id, comment): self.calls.append(("comment", entity_type, entity_id, comment))
    async def activity_has_marker(self, marker): return False
    async def add_activity(self, fields): self.calls.append(("activity", fields)); return 14
    async def get_invoice(self, invoice_id): self.calls.append(("get_invoice", invoice_id)); return self.invoice_fields
    async def update_invoice(self, invoice_id, fields): self.calls.append(("send", invoice_id, fields)); self.invoice_fields.update(fields)


def make_transaction():
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(), idempotency_key=uuid4(), user_id=uuid4(), employee_external_id="1", employee_display_name="Иван",
        address_id=100, address_text="Полный адрес", apartment="12А", product_id=123, product_title="Услуга",
        catalog_amount=Decimal("1500.00"), actual_amount=Decimal("1700.00"), currency="RUB",
        first_name="Иван", second_name=None, last_name="Иванов", phone_normalized="+79991234567",
        bitrix_lead_id=None, bitrix_contact_id=None, bitrix_invoice_id=None, bitrix_product_row_id=None,
        bitrix_payment_id=None, payment_product_linked=False, payment_url=None, payment_short_url=None, payment_qr=None,
        status="draft", current_step="draft", send_status=None, candidate_snapshot=[], last_error_code=None,
        last_error_message=None, retry_count=0, next_attempt_at=now, formation_timeline_created=False,
        formation_activity_created=False, paid_timeline_created=False, paid_activity_created=False, created_at=now, updated_at=now,
        paid_at=None, expires_at=now + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_orchestrator_creates_each_bitrix_entity_once_and_keeps_link_when_sms_unconfigured() -> None:
    transaction = make_transaction(); repository = FakeRepository(transaction); bitrix = FakeBitrix()
    service = PaymentService(Settings(), repository, SimpleNamespace(), FakeResolver(), bitrix)
    result = await service.process(transaction.id)
    assert result.status == "send_failed"
    assert result.payment_short_url == "https://pay/s"
    assert result.bitrix_contact_id == 5 and result.bitrix_lead_id == 6
    comments = [call for call in bitrix.calls if call[0] == "comment"]
    assert len(comments) == 2
    assert all("https://pay" not in call[3] for call in comments)
    writes_before = list(bitrix.calls)
    await service.process(transaction.id)
    assert bitrix.calls == writes_before


@pytest.mark.asyncio
async def test_custom_price_is_sent_as_product_row_price() -> None:
    transaction = make_transaction(); repository = FakeRepository(transaction); bitrix = FakeBitrix()
    await PaymentService(Settings(), repository, SimpleNamespace(), FakeResolver(), bitrix).process(transaction.id)
    row = next(call for call in bitrix.calls if call[0] == "row")
    assert row[2]["productId"] == 123
    assert row[2]["price"] == "1700.00"
    assert set(row[2]) == {"productId", "productName", "price", "quantity"}


@pytest.mark.asyncio
async def test_send_trigger_recovers_remote_write_and_explicit_resend_forces_write() -> None:
    transaction = make_transaction()
    transaction.bitrix_contact_id = 5; transaction.bitrix_lead_id = 6
    transaction.bitrix_invoice_id = 10; transaction.bitrix_product_row_id = 11
    transaction.bitrix_payment_id = 12; transaction.payment_product_linked = True
    transaction.payment_url = "https://pay/full"; transaction.payment_short_url = "https://pay/s"
    transaction.status = "link_created"; transaction.current_step = "link_created"
    transaction.formation_timeline_created = True
    settings = Settings(bx24_payment_link_field="ufCrmPaymentLink", bx24_payment_send_trigger="stageId=DT31_1:SENT")
    repository = FakeRepository(transaction); bitrix = FakeBitrix()
    bitrix.invoice_fields = {"ufCrmPaymentLink": "https://pay/s", "stageId": "DT31_1:SENT"}
    service = PaymentService(settings, repository, SimpleNamespace(), FakeResolver(), bitrix)

    result = await service.process(transaction.id)
    assert result.status == "send_queued"
    assert not any(call[0] == "send" for call in bitrix.calls)

    actor = SimpleNamespace(user_id=transaction.user_id)
    await service.resend(actor, transaction.id)
    assert [call[0] for call in bitrix.calls].count("send") == 1


@pytest.mark.asyncio
async def test_payment_card_is_not_available_to_another_employee() -> None:
    transaction = make_transaction(); repository = FakeRepository(transaction); bitrix = FakeBitrix()
    service = PaymentService(Settings(), repository, SimpleNamespace(), FakeResolver(), bitrix)
    with pytest.raises(PaymentNotFoundError):
        await service.get(SimpleNamespace(user_id=uuid4()), transaction.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["invoice", "row", "payment", "payment_product", "timeline", "activity", "send"])
async def test_orchestrator_recovers_unknown_result_after_each_external_write(failure_stage: str) -> None:
    class RecoveringBitrix(FakeBitrix):
        def __init__(self):
            super().__init__()
            self.failure_stage = failure_stage; self.failed = False
            self.remote_invoice = None; self.remote_row = None; self.remote_payment = None
            self.remote_linked = False; self.remote_comments = []; self.remote_activities = []

        def fail_after_write(self, stage):
            if self.failure_stage == stage and not self.failed:
                self.failed = True
                raise Bitrix24Error()

        async def find_invoice_by_xml_id(self, xml_id): return self.remote_invoice
        async def create_invoice(self, fields):
            self.calls.append(("invoice", fields)); self.remote_invoice = 10; self.fail_after_write("invoice"); return 10
        async def find_product_row(self, invoice_id, product_id): return self.remote_row
        async def add_product_row(self, invoice_id, fields):
            self.calls.append(("row", invoice_id, fields)); self.remote_row = 11; self.fail_after_write("row"); return 11
        async def find_payment(self, invoice_id): return self.remote_payment
        async def create_payment(self, invoice_id):
            self.calls.append(("payment", invoice_id)); self.remote_payment = 12; self.fail_after_write("payment"); return 12
        async def payment_product_linked(self, payment_id, row_id): return self.remote_linked
        async def add_payment_product(self, payment_id, row_id):
            self.calls.append(("payment_product", payment_id, row_id)); self.remote_linked = True
            self.fail_after_write("payment_product"); return 13
        async def timeline_has_marker(self, entity_type, entity_id, marker):
            return any(item[:2] == (entity_type, entity_id) and marker in item[2] for item in self.remote_comments)
        async def timeline_comment(self, entity_type, entity_id, comment):
            self.calls.append(("comment", entity_type, entity_id, comment)); self.remote_comments.append((entity_type, entity_id, comment))
            self.fail_after_write("timeline")
        async def activity_has_marker(self, marker): return any(marker in item["DESCRIPTION"] for item in self.remote_activities)
        async def add_activity(self, fields):
            self.calls.append(("activity", fields)); self.remote_activities.append(fields); self.fail_after_write("activity"); return 14
        async def update_invoice(self, invoice_id, fields):
            self.calls.append(("send", invoice_id, fields)); self.invoice_fields.update(fields); self.fail_after_write("send")

    settings = Settings(**({
        "bx24_payment_link_field": "ufCrmPaymentLink",
        "bx24_payment_send_trigger": "stageId=DT31_1:SENT",
    } if failure_stage == "send" else {}))
    transaction = make_transaction(); repository = FakeRepository(transaction); bitrix = RecoveringBitrix()
    service = PaymentService(settings, repository, SimpleNamespace(), FakeResolver(), bitrix)
    with pytest.raises(Bitrix24Error):
        await service.process(transaction.id)
    await service.mark_failure(transaction.id, Bitrix24Error())
    result = await service.process(transaction.id)

    assert result.status == ("send_queued" if failure_stage == "send" else "send_failed")
    expected_tag = {"timeline": "comment", "activity": "activity"}.get(failure_stage, failure_stage)
    if failure_stage == "timeline":
        comments = [call for call in bitrix.calls if call[0] == "comment"]
        assert {(call[1], call[2]) for call in comments} == {("contact", 5), ("lead", 6)}
    else:
        assert [call[0] for call in bitrix.calls].count(expected_tag) == 1
