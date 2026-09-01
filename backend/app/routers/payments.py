import json
import logging
import secrets
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.config import Settings, get_settings
from app.dependencies import actor_from_session, require_csrf, require_session
from app.errors import PermissionDeniedError
from app.logging import audit
from app.permissions import has_permission
from app.schemas import (
    PaymentAcceptedResponse,
    PaymentAddress,
    PaymentClientSelectionRequest,
    PaymentCreateRequest,
    PaymentProduct,
    PaymentRecentResponse,
    PaymentTransactionResponse,
    SessionData,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_payments(actor) -> None:
    if not has_permission(actor.permissions or {}, "client", "create"):
        raise PermissionDeniedError()


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
        await request.app.state.payment_status.handle(payment_id)
        audit(logger, "payment.webhook.accepted", payment_id=payment_id)
    return {"accepted": True}
