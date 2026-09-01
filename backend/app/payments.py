from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.actors import Actor
from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.errors import ApiError, Bitrix24Error, PaymentClientAmbiguousError, PaymentNotFoundError, PaymentStateError, PermissionDeniedError
from app.models import PaymentTransaction
from app.payment_catalog import PaymentProductCatalog
from app.payment_client_resolver import AmbiguousClient, PaymentClientResolver
from app.repositories import PaymentRepository
from app.schemas import (
    PaymentAcceptedResponse,
    PaymentCandidate,
    PaymentCreateRequest,
    PaymentRecentResponse,
    PaymentTransactionResponse,
)
from app.roles import UserRole


WORK_STATES = {"draft", "resolving_client", "client_resolved", "invoice_created", "product_added", "payment_created", "link_created"}
POLL_STATES = {"send_queued", "sent", "send_failed"}
TERMINAL_STATES = {"paid", "expired", "canceled", "failed"}


class PaymentService:
    def __init__(
        self,
        settings: Settings,
        repository: PaymentRepository,
        catalog: PaymentProductCatalog,
        resolver: PaymentClientResolver,
        bitrix: Bitrix24Client,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._catalog = catalog
        self._resolver = resolver
        self._bitrix = bitrix

    async def create(self, actor: Actor, employee_name: str | None, payload: PaymentCreateRequest) -> tuple[PaymentAcceptedResponse, bool]:
        product = await self._catalog.get(payload.product_id)
        self._validate_amount(payload.amount, product.default_amount)
        now = datetime.now(UTC)
        transaction, created = await self._repository.create_or_get(
            idempotency_key=payload.idempotency_key,
            values={
                "user_id": actor.user_id,
                "employee_external_id": actor.techportal_user_id,
                "employee_display_name": employee_name,
                "address_id": payload.address.locid if payload.address else None,
                "address_text": payload.address.loctext if payload.address else None,
                "apartment": payload.apartment,
                "product_id": product.product_id,
                "product_title": product.title,
                "catalog_amount": product.default_amount,
                "actual_amount": payload.amount,
                "currency": product.currency,
                "first_name": payload.first_name,
                "second_name": payload.second_name,
                "last_name": payload.last_name,
                "phone_normalized": payload.phone,
                "status": "draft",
                "current_step": "draft",
                "next_attempt_at": now,
                "expires_at": now + timedelta(seconds=self._settings.bx24_payment_expires_seconds),
            },
        )
        if created:
            await self._repository.add_event(transaction.id, "payment.created", {"product_id": product.product_id, "amount": str(payload.amount), "currency": product.currency})
        return PaymentAcceptedResponse(id=transaction.id, status=transaction.status), created

    async def get(self, actor: Actor, transaction_id: UUID) -> PaymentTransactionResponse:
        transaction = await self._owned(actor, transaction_id)
        return self.to_response(transaction)

    async def recent(self, actor: Actor) -> list[PaymentRecentResponse]:
        rows = await self._repository.list_recent(actor.user_id)
        return [PaymentRecentResponse(
            id=item.id, status=item.status, product_title=item.product_title,
            actual_amount=item.actual_amount, currency=item.currency, created_at=item.created_at,
        ) for item in rows]

    async def select_client(self, actor: Actor, transaction_id: UUID, entity_type: str, entity_id: int) -> PaymentTransactionResponse:
        transaction = await self._owned(actor, transaction_id)
        if transaction.status != "client_selection_required":
            raise PaymentStateError()
        try:
            resolved = await self._resolver.resolve_selected(transaction, entity_type, entity_id)
        except ValueError as exc:
            raise PaymentStateError("Выбранный клиент отсутствует среди кандидатов") from exc
        transaction = await self._repository.update(
            transaction.id, bitrix_contact_id=resolved.contact_id, bitrix_lead_id=resolved.lead_id,
            status="client_resolved", current_step="client_resolved", candidate_snapshot=[], next_attempt_at=datetime.now(UTC),
        )
        await self._repository.add_event(transaction.id, "client.selected", {"entity_type": entity_type, "entity_id": entity_id})
        return self.to_response(transaction)

    async def resend(self, actor: Actor, transaction_id: UUID) -> PaymentTransactionResponse:
        transaction = await self._owned(actor, transaction_id)
        if transaction.status in TERMINAL_STATES or transaction.status == "failed":
            raise PaymentStateError("Повторная отправка недоступна для завершённой операции")
        if not transaction.bitrix_invoice_id or not (transaction.payment_short_url or transaction.payment_url):
            raise PaymentStateError("Платёжная ссылка ещё не сформирована")
        status = await self._trigger_send(transaction, force=True)
        transaction = await self._repository.update(transaction.id, status=status, current_step="send", send_status=status, next_attempt_at=datetime.now(UTC))
        await self._repository.add_event(transaction.id, "payment.resend_requested")
        return self.to_response(transaction)

    async def resume_failed_as_admin(self, actor: Actor, transaction_id: UUID) -> PaymentTransactionResponse:
        if actor.role is not UserRole.ADMIN:
            raise PermissionDeniedError()
        transaction = await self._repository.get(transaction_id)
        if transaction is None:
            raise PaymentNotFoundError()
        if transaction.status != "failed":
            raise PaymentStateError("Возобновить можно только окончательно неуспешную операцию")
        transaction = await self._repository.update(
            transaction.id, status="draft", current_step="resume_queued", retry_count=0,
            last_error_code=None, last_error_message=None, next_attempt_at=datetime.now(UTC),
        )
        await self._repository.add_event(transaction.id, "payment.admin_resumed", {"admin_user_id": str(actor.user_id)})
        return self.to_response(transaction)

    async def cancel(self, actor: Actor, transaction_id: UUID) -> PaymentTransactionResponse:
        transaction = await self._owned(actor, transaction_id)
        if transaction.status in TERMINAL_STATES:
            raise PaymentStateError("Отмена недоступна для завершённой операции")
        if transaction.bitrix_payment_id:
            payment = await self._bitrix.get_payment(transaction.bitrix_payment_id)
            if payment.get("paid", payment.get("PAID")) in {True, "Y", 1, "1"}:
                raise PaymentStateError("Оплата уже подтверждена Битрикс24")
            await self._bitrix.delete_payment(transaction.bitrix_payment_id)
        transaction = await self._repository.update(
            transaction.id, status="canceled", current_step="canceled", next_attempt_at=None,
        )
        await self._repository.add_event(transaction.id, "payment.canceled", {"payment_id": transaction.bitrix_payment_id})
        return self.to_response(transaction)

    async def process(self, transaction_id: UUID) -> PaymentTransaction:
        transaction = await self._repository.get(transaction_id)
        if transaction is None or transaction.status in TERMINAL_STATES or transaction.status == "client_selection_required":
            if transaction is None:
                raise PaymentNotFoundError()
            return transaction
        if transaction.status in {"draft", "resolving_client"} and not transaction.bitrix_contact_id:
            transaction = await self._repository.update(transaction.id, status="resolving_client", current_step="resolving_client")
            try:
                resolved = await self._resolver.resolve(transaction)
            except AmbiguousClient as exc:
                snapshot = [candidate.model_dump() for candidate in exc.candidates]
                transaction = await self._repository.update(transaction.id, status="client_selection_required", current_step="client_selection", candidate_snapshot=snapshot, next_attempt_at=None)
                await self._repository.add_event(transaction.id, "client.ambiguous", {"candidate_count": len(snapshot)})
                return transaction
            transaction = await self._repository.update(transaction.id, bitrix_contact_id=resolved.contact_id, bitrix_lead_id=resolved.lead_id, status="client_resolved", current_step="client_resolved", last_error_code=None, last_error_message=None)
            await self._repository.add_event(transaction.id, "client.resolved", {"contact_id": resolved.contact_id, "lead_id": resolved.lead_id})

        if not transaction.bitrix_invoice_id:
            invoice_id = await self._bitrix.find_invoice_by_xml_id(str(transaction.id))
            if invoice_id is None:
                invoice_id = await self._bitrix.create_invoice({
                    "title": f"Оплата {transaction.product_title}", "contactId": transaction.bitrix_contact_id,
                    "contactIds": [transaction.bitrix_contact_id], "opportunity": str(transaction.actual_amount),
                    "currencyId": transaction.currency, "xmlId": str(transaction.id),
                })
            transaction = await self._repository.update(transaction.id, bitrix_invoice_id=invoice_id, status="invoice_created", current_step="invoice_created")
            await self._repository.add_event(transaction.id, "invoice.created", {"invoice_id": invoice_id})

        if not transaction.bitrix_product_row_id:
            row_id = await self._bitrix.find_product_row(transaction.bitrix_invoice_id, transaction.product_id)
            if row_id is None:
                row_id = await self._bitrix.add_product_row(transaction.bitrix_invoice_id, {
                    "productId": transaction.product_id, "productName": transaction.product_title,
                    "price": str(transaction.actual_amount), "quantity": 1,
                })
            transaction = await self._repository.update(transaction.id, bitrix_product_row_id=row_id, status="product_added", current_step="product_added")
            await self._repository.add_event(transaction.id, "product.added", {"product_row_id": row_id})

        if not transaction.bitrix_payment_id:
            payment_id = await self._bitrix.find_payment(transaction.bitrix_invoice_id)
            if payment_id is None:
                payment_id = await self._bitrix.create_payment(transaction.bitrix_invoice_id)
            transaction = await self._repository.update(transaction.id, bitrix_payment_id=payment_id, status="payment_created", current_step="payment_created")
            await self._repository.add_event(transaction.id, "payment_document.created", {"payment_id": payment_id})

        if not transaction.payment_product_linked:
            linked = await self._bitrix.payment_product_linked(transaction.bitrix_payment_id, transaction.bitrix_product_row_id)
            if not linked:
                await self._bitrix.add_payment_product(transaction.bitrix_payment_id, transaction.bitrix_product_row_id)
            transaction = await self._repository.update(transaction.id, payment_product_linked=True)
            await self._repository.add_event(transaction.id, "payment.product_linked", {"product_row_id": transaction.bitrix_product_row_id})

        if not transaction.payment_url and not transaction.payment_short_url:
            links = await self._bitrix.payment_public_url(transaction.bitrix_payment_id)
            transaction = await self._repository.update(
                transaction.id, payment_url=links["url"], payment_short_url=links["short_url"], payment_qr=links["qr"],
                status="link_created", current_step="link_created",
            )
            await self._repository.add_event(transaction.id, "payment.link_created")

        if not transaction.formation_timeline_created:
            await self._formation_timeline(transaction)
            transaction = await self._repository.update(transaction.id, formation_timeline_created=True)

        if transaction.status == "link_created":
            send_status = await self._trigger_send(transaction)
            transaction = await self._repository.update(transaction.id, status=send_status, current_step="send", send_status=send_status, next_attempt_at=datetime.now(UTC) + timedelta(seconds=self._settings.bx24_payment_poll_interval_seconds))
            await self._repository.add_event(transaction.id, "payment.send_state", {"send_status": send_status})
        if transaction.status in POLL_STATES and not transaction.formation_activity_created:
            await self._formation_activity(transaction)
            transaction = await self._repository.update(transaction.id, formation_activity_created=True)
        if transaction.last_error_code or transaction.last_error_message:
            transaction = await self._repository.update(
                transaction.id,
                last_error_code=None,
                last_error_message=None,
            )
        return transaction

    async def mark_failure(self, transaction_id: UUID, exc: Exception) -> PaymentTransaction:
        transaction = await self._repository.get(transaction_id)
        if transaction is None:
            raise PaymentNotFoundError()
        retry_count = transaction.retry_count + 1
        retryable = retry_count <= self._settings.bx24_worker_max_retries
        delay = min(300, 2 ** min(retry_count, 8))
        code = exc.code if isinstance(exc, ApiError) else "PAYMENT_PROCESSING_FAILED"
        return await self._repository.update(
            transaction_id, status=transaction.status if retryable else "failed",
            last_error_code=code, last_error_message="Внешняя операция временно не выполнена",
            retry_count=retry_count, next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay) if retryable else None,
        )

    def _validate_amount(self, amount: Decimal, catalog_amount: Decimal) -> None:
        if amount < self._settings.bx24_payment_min_amount or amount > self._settings.bx24_payment_max_amount:
            raise Bitrix24Error("PAYMENT_AMOUNT_OUT_OF_RANGE", "Сумма выходит за разрешённые пределы", 422)
        if not self._settings.bx24_payment_allow_price_override and amount != catalog_amount:
            raise Bitrix24Error("PAYMENT_PRICE_OVERRIDE_FORBIDDEN", "Изменение цены запрещено", 422)

    async def _trigger_send(self, transaction: PaymentTransaction, *, force: bool = False) -> str:
        if not self._settings.bx24_payment_link_field:
            return "send_failed"
        fields: dict[str, str] = {self._settings.bx24_payment_link_field: transaction.payment_short_url or transaction.payment_url or ""}
        trigger = self._settings.bx24_payment_send_trigger
        if "=" in trigger:
            field, value = trigger.split("=", 1)
            fields[field.strip()] = value.strip()
        elif trigger:
            fields["stageId"] = trigger
        if not force:
            invoice = await self._bitrix.get_invoice(transaction.bitrix_invoice_id)
            if all(str(invoice.get(field, "")) == str(value) for field, value in fields.items()):
                return "send_queued" if trigger else "send_failed"
        await self._bitrix.update_invoice(transaction.bitrix_invoice_id, fields)
        return "send_queued" if trigger else "send_failed"

    async def _formation_timeline(self, transaction: PaymentTransaction) -> None:
        marker = f"Операция ТехПортала: {transaction.id}; событие: payment-created"
        comment = (
            f"Сформирована оплата\nУслуга: {transaction.product_title}\n"
            f"Сумма: {transaction.actual_amount} {transaction.currency}\n{marker}"
        )
        if not await self._bitrix.timeline_has_marker("contact", transaction.bitrix_contact_id, marker):
            await self._bitrix.timeline_comment("contact", transaction.bitrix_contact_id, comment)
        if transaction.bitrix_lead_id and not await self._bitrix.timeline_has_marker("lead", transaction.bitrix_lead_id, marker):
            await self._bitrix.timeline_comment("lead", transaction.bitrix_lead_id, comment)

    async def _formation_activity(self, transaction: PaymentTransaction) -> None:
        marker = f"Операция ТехПортала: {transaction.id}; событие: payment-link-created"
        if await self._bitrix.activity_has_marker(marker):
            return
        bindings = [{"OWNER_TYPE_ID": 3, "OWNER_ID": transaction.bitrix_contact_id}]
        if transaction.bitrix_lead_id:
            bindings.append({"OWNER_TYPE_ID": 1, "OWNER_ID": transaction.bitrix_lead_id})
        send_failed = transaction.send_status == "send_failed"
        await self._bitrix.add_activity({
            "SUBJECT": "Отправить ссылку на оплату клиенту" if send_failed else "Ссылка на оплату подготовлена",
            "DESCRIPTION": marker,
            "COMPLETED": "N" if send_failed else "Y",
            "BINDINGS": bindings,
        })

    async def _owned(self, actor: Actor, transaction_id: UUID) -> PaymentTransaction:
        transaction = await self._repository.get(transaction_id, actor.user_id)
        if transaction is None:
            raise PaymentNotFoundError()
        return transaction

    @staticmethod
    def to_response(transaction: PaymentTransaction) -> PaymentTransactionResponse:
        return PaymentTransactionResponse(
            id=transaction.id, status=transaction.status, current_step=transaction.current_step,
            send_status=transaction.send_status, product_title=transaction.product_title,
            catalog_amount=transaction.catalog_amount, actual_amount=transaction.actual_amount,
            currency=transaction.currency, payment_url=transaction.payment_url,
            payment_short_url=transaction.payment_short_url, payment_qr=transaction.payment_qr,
            candidates=[PaymentCandidate.model_validate(item) for item in transaction.candidate_snapshot],
            error_code=transaction.last_error_code, error_message=transaction.last_error_message,
            created_at=transaction.created_at, updated_at=transaction.updated_at,
        )
