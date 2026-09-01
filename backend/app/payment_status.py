from datetime import UTC, datetime, timedelta

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentTransaction
from app.repositories import PaymentRepository


class PaymentStatusHandler:
    def __init__(self, settings: Settings, repository: PaymentRepository, bitrix: Bitrix24Client) -> None:
        self._settings = settings
        self._repository = repository
        self._bitrix = bitrix

    async def handle(self, payment_id: int) -> PaymentTransaction | None:
        transaction = await self._repository.get_by_payment_id(payment_id)
        if transaction is None:
            return None
        was_paid = transaction.status == "paid"
        payment = await self._bitrix.get_payment(payment_id) if not was_paid else {"paid": True}
        metadata = {
            "bitrix_payment_account_number": str(payment.get("accountNumber")) if payment.get("accountNumber") else None,
            "bitrix_pay_system_id": int(payment["paySystemId"]) if payment.get("paySystemId") else None,
            "bitrix_pay_system_name": str(payment.get("paySystemName")) if payment.get("paySystemName") else None,
        }
        if any(value is not None for value in metadata.values()):
            transaction = await self._repository.update(transaction.id, **metadata)
        paid = payment.get("paid", payment.get("PAID")) in {True, "Y", 1, "1"}
        if not paid:
            now = datetime.now(UTC)
            if transaction.expires_at <= now:
                transaction = await self._repository.update(
                    transaction.id, status="expired", current_step="expired", next_attempt_at=None,
                )
                await self._repository.add_event(transaction.id, "payment.expired", {"payment_id": payment_id})
                return transaction
            return await self._repository.update(
                transaction.id,
                next_attempt_at=now + timedelta(seconds=self._settings.bx24_payment_poll_interval_seconds),
            )
        if not transaction.paid_timeline_created:
            marker = f"Операция ТехПортала: {transaction.id}; событие: payment-paid"
            comment = f"Оплата подтверждена\nСумма: {transaction.actual_amount} {transaction.currency}\n{marker}"
            if not await self._bitrix.timeline_has_marker("contact", transaction.bitrix_contact_id, marker):
                await self._bitrix.timeline_comment("contact", transaction.bitrix_contact_id, comment)
            if transaction.bitrix_lead_id and not await self._bitrix.timeline_has_marker("lead", transaction.bitrix_lead_id, marker):
                await self._bitrix.timeline_comment("lead", transaction.bitrix_lead_id, comment)
            transaction = await self._repository.update(transaction.id, paid_timeline_created=True)
        if not transaction.paid_activity_created:
            marker = f"Операция ТехПортала: {transaction.id}; событие: payment-paid"
            bindings = [{"OWNER_TYPE_ID": 3, "OWNER_ID": transaction.bitrix_contact_id}]
            if transaction.bitrix_lead_id:
                bindings.append({"OWNER_TYPE_ID": 1, "OWNER_ID": transaction.bitrix_lead_id})
            if not await self._bitrix.activity_has_marker(marker):
                await self._bitrix.add_activity({
                    "SUBJECT": f"Оплата {transaction.product_title} подтверждена",
                    "DESCRIPTION": marker, "COMPLETED": "Y", "BINDINGS": bindings,
                })
            transaction = await self._repository.update(transaction.id, paid_activity_created=True)
        if transaction.status != "paid":
            transaction = await self._repository.update(
                transaction.id, status="paid", current_step="paid", paid_at=datetime.now(UTC),
                last_error_code=None, last_error_message=None, next_attempt_at=None,
            )
            await self._repository.add_event(transaction.id, "payment.paid", {"payment_id": payment_id})
        if not was_paid:
            pending_marker = f"Операция ТехПортала: {transaction.id}; событие: payment-link-created"
            pending_activity_id = await self._bitrix.activity_id_by_marker(pending_marker)
            if pending_activity_id is not None:
                await self._bitrix.complete_activity(pending_activity_id)
        return transaction
