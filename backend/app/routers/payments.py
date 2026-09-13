import json
import logging
import secrets
from urllib.parse import parse_qs
from uuid import UUID
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, status

from app.config import Settings, get_settings
from app.dependencies import actor_from_session, require_csrf, require_session, require_admin
from app.errors import PermissionDeniedError
from app.logging import audit, redact
from app.permissions import has_permission
from app.payment_telemetry import elapsed, current_trace
from time import perf_counter
from app.schemas import (
    PaymentAcceptedResponse,
    PaymentAddress,
    PaymentClientSelectionRequest,
    PaymentCreateRequest,
    PaymentProduct,
    PaymentRecentResponse,
    PaymentTransactionResponse,
    SessionData,
    AdminPaymentDetail, AdminPaymentEvent, AdminPaymentItem, AdminPaymentListResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def _admin_item(row) -> AdminPaymentItem:
    return AdminPaymentItem(id=row.id, paid_at=row.paid_at, actual_amount=row.actual_amount, currency=row.currency, product_title=row.product_title, status="paid", phone=row.phone_normalized, email=row.email, employee_display_name=row.employee_display_name, employee_external_id=row.employee_external_id, bitrix_lead_id=row.bitrix_lead_id, bitrix_contact_id=row.bitrix_contact_id, bitrix_invoice_id=row.bitrix_invoice_id, bitrix_payment_id=row.bitrix_payment_id, bitrix_payment_account_number=row.bitrix_payment_account_number, bitrix_pay_system_name=row.bitrix_pay_system_name)


def _date_bounds(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
    today = datetime.now(MOSCOW).date()
    start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today - timedelta(days=29)
    end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
    if start > end:
        raise ValueError("Начало периода не может быть позже конца")
    return datetime.combine(start, time.min, MOSCOW).astimezone(UTC), datetime.combine(end + timedelta(days=1), time.min, MOSCOW).astimezone(UTC)


def _require_payments(actor) -> None:
    if not has_permission(actor.permissions or {}, "tickets", "execution"):
        raise PermissionDeniedError()


@router.get("/api/admin/payments", response_model=AdminPaymentListResponse)
async def admin_payments(request: Request, date_from: str | None = None, date_to: str | None = None, phone: str | None = None, employee: str | None = None, page: int = 1, page_size: int = 50, admin=Depends(require_admin)) -> AdminPaymentListResponse:
    if page < 1 or page_size < 1 or page_size > 100:
        from app.errors import ApiError
        raise ApiError(422, "VALIDATION_ERROR", "Некорректная пагинация")
    try:
        start, end = _date_bounds(date_from, date_to)
    except ValueError as exc:
        from app.errors import ApiError
        raise ApiError(422, "VALIDATION_ERROR", str(exc)) from exc
    _, _, actor = admin
    rows, total = await request.app.state.payment_repository.admin_list(date_from=start, date_to=end, phone=phone, employee=employee, page=page, page_size=page_size)
    audit(logger, "payment.admin.list.viewed", user_id=str(actor.user_id), date_from=date_from, date_to=date_to, page=page, page_size=page_size, count=total)
    return AdminPaymentListResponse(items=[_admin_item(row) for row in rows], page=page, page_size=page_size, total=total)


@router.get("/api/admin/payments/{transaction_id}", response_model=AdminPaymentDetail)
async def admin_payment(transaction_id: UUID, request: Request, admin=Depends(require_admin)) -> AdminPaymentDetail:
    _, _, actor = admin
    result = await request.app.state.payment_repository.admin_get(transaction_id)
    if result is None:
        from app.errors import ApiError
        raise ApiError(404, "PAYMENT_NOT_FOUND", "Оплаченная операция не найдена")
    row, events = result
    audit(logger, "payment.admin.detail.viewed", user_id=str(actor.user_id), transaction_id=str(transaction_id))
    item = _admin_item(row)
    return AdminPaymentDetail(**item.model_dump(), catalog_amount=row.catalog_amount, address_text=row.address_text, apartment=row.apartment, created_at=row.created_at, updated_at=row.updated_at, events=[AdminPaymentEvent(id=event.id, event_type=event.event_type, created_at=event.created_at, payload=redact(event.safe_payload)) for event in events])


@router.get("/api/payments/addresses", response_model=list[PaymentAddress])
async def addresses(request: Request, session_pair: tuple[str, SessionData] = Depends(require_session)) -> list[PaymentAddress]:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    return await request.app.state.payment_addresses.list()


@router.get("/api/payments/products", response_model=list[PaymentProduct])
async def products(request: Request, session_pair: tuple[str, SessionData] = Depends(require_session)) -> list[PaymentProduct]:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    return await request.app.state.payment_catalog.list()


@router.post("/api/payments", response_model=PaymentAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_payment(payload: PaymentCreateRequest, request: Request, session_pair: tuple[str, SessionData] = Depends(require_csrf)) -> PaymentAcceptedResponse:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    if current_trace.get() is not None:
        elapsed(request.state.timing_started_at, "request_validation", duration_ms=(perf_counter() - request.state.timing_started) * 1000)
    response, created = await request.app.state.payment_service.create(actor, session_pair[1].user.first_name, payload)
    audit(
        logger, "payment.request.accepted", user_id=str(actor.user_id), transaction_id=str(response.id),
        product_id=payload.product_id, amount=str(payload.amount), created=created,
    )
    return response


@router.get("/api/payments/recent", response_model=list[PaymentRecentResponse])
async def recent_payments(request: Request, session_pair: tuple[str, SessionData] = Depends(require_session)) -> list[PaymentRecentResponse]:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    return await request.app.state.payment_service.recent(actor)


@router.get("/api/payments/{transaction_id}", response_model=PaymentTransactionResponse)
async def payment(transaction_id: UUID, request: Request, session_pair: tuple[str, SessionData] = Depends(require_session)) -> PaymentTransactionResponse:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    return await request.app.state.payment_service.get(actor, transaction_id)


@router.post("/api/payments/{transaction_id}/client-selection", response_model=PaymentTransactionResponse)
async def select_client(transaction_id: UUID, payload: PaymentClientSelectionRequest, request: Request, session_pair: tuple[str, SessionData] = Depends(require_csrf)) -> PaymentTransactionResponse:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    response = await request.app.state.payment_service.select_client(actor, transaction_id, payload.entity_type, payload.entity_id)
    audit(logger, "payment.client.selected", user_id=str(actor.user_id), transaction_id=str(transaction_id), entity_type=payload.entity_type, entity_id=payload.entity_id)
    return response


@router.post("/api/payments/{transaction_id}/resend", response_model=PaymentTransactionResponse)
async def resend(transaction_id: UUID, request: Request, session_pair: tuple[str, SessionData] = Depends(require_csrf)) -> PaymentTransactionResponse:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    response = await request.app.state.payment_service.resend(actor, transaction_id)
    audit(logger, "payment.resend.requested", user_id=str(actor.user_id), transaction_id=str(transaction_id))
    return response


@router.post("/api/admin/payments/{transaction_id}/resume", response_model=PaymentTransactionResponse)
async def resume_failed_payment(transaction_id: UUID, request: Request, session_pair: tuple[str, SessionData] = Depends(require_csrf)) -> PaymentTransactionResponse:
    actor = await actor_from_session(request, session_pair[1])
    response = await request.app.state.payment_service.resume_failed_as_admin(actor, transaction_id)
    audit(logger, "payment.admin_resumed", user_id=str(actor.user_id), transaction_id=str(transaction_id))
    return response


@router.post("/api/payments/{transaction_id}/cancel", response_model=PaymentTransactionResponse)
async def cancel_payment(transaction_id: UUID, request: Request, session_pair: tuple[str, SessionData] = Depends(require_csrf)) -> PaymentTransactionResponse:
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    response = await request.app.state.payment_service.cancel(actor, transaction_id)
    audit(logger, "payment.canceled", user_id=str(actor.user_id), transaction_id=str(transaction_id))
    return response


@router.post("/api/webhooks/bitrix24/payments", status_code=status.HTTP_202_ACCEPTED)
async def bitrix_payment_webhook(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    body = await request.body()
    payload: dict = {}
    parsed: dict[str, list[str]] = {}
    try:
        decoded = json.loads(body) if body else {}
        payload = decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        parsed = parse_qs(body.decode("utf-8", errors="ignore"))
    expected = settings.bx24_payment_webhook_token.get_secret_value()
    auth = payload.get("auth", {}) if isinstance(payload.get("auth"), dict) else {}
    supplied = (
        request.headers.get("x-bitrix-token", "")
        or str(auth.get("application_token", ""))
        or parsed.get("auth[application_token]", [""])[0]
    )
    if not expected or not secrets.compare_digest(supplied, expected):
        raise PermissionDeniedError()
    payment_id: int | None = None
    try:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        fields = data.get("FIELDS", data) if isinstance(data, dict) else {}
        raw_id = fields.get("ID", fields.get("id")) if isinstance(fields, dict) else None
        payment_id = int(raw_id) if raw_id is not None else None
    except (ValueError, TypeError):
        payment_id = None
    if payment_id is None and parsed:
        raw = parsed.get("data[FIELDS][ID]", parsed.get("data[ID]", [None]))[0]
        payment_id = int(raw) if raw and str(raw).isdigit() else None
    if payment_id is not None:
        repository = getattr(request.app.state, 'payment_repository', None)
        if getattr(repository, 'event_queue', None) is not None:
            await repository.event_queue.receive_callback(payment_id)
        else:
            await request.app.state.payment_status.handle(payment_id)
        audit(logger, "payment.webhook.accepted", payment_id=payment_id)
    return {"accepted": True}
