"""Admin-only diagnostics for all payment states; browser metrics are untrusted."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dependencies import actor_from_session, require_admin, require_csrf
from app.config import Settings, get_settings
from app.errors import ApiError
from app.logging import audit
from app.payment_telemetry import logger
from app.routers.payments import _date_bounds, _require_payments

router = APIRouter()


class BrowserTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted_ms: float | None = Field(default=None, ge=0, le=86400000, allow_inf_nan=False)
    link_ms: float | None = Field(default=None, ge=0, le=86400000, allow_inf_nan=False)
    qr_ms: float = Field(ge=0, le=86400000, allow_inf_nan=False)
    qr_error: bool = False
    background: bool = False
    restored: bool = False

    @model_validator(mode="after")
    def ordered(self):
        values = [v for v in (self.accepted_ms, self.link_ms, self.qr_ms) if v is not None]
        if values != sorted(values):
            raise ValueError("Timing order is invalid")
        return self


@router.post("/api/payments/{transaction_id}/timing", status_code=204)
async def browser_timing(transaction_id: UUID, payload: BrowserTiming, request: Request, session_pair=Depends(require_csrf), settings: Settings = Depends(get_settings)):
    actor = await actor_from_session(request, session_pair[1])
    _require_payments(actor)
    await request.app.state.payment_service.get(actor, transaction_id)
    if not settings.payment_telemetry_enabled:
        return
    rows = []
    for name, duration in (("browser_accept", payload.accepted_ms), ("browser_link", payload.link_ms), ("browser_qr", payload.qr_ms)):
        if duration is not None:
            rows.append(dict(id=uuid4(), parent_id=None, name=name, kind="browser", started_at=datetime.now(UTC), duration_ms=duration,
                outcome="error" if name == "browser_qr" and payload.qr_error else "ok",
                details={"background": payload.background, "restored": payload.restored, "untrusted": True}))
    try:
        async with asyncio.timeout(2):
            await request.app.state.payment_repository.save_browser_timing(transaction_id, rows, uuid4())
    except Exception:
        logger.warning("payment.telemetry.browser_write_failed")


def summary(row, spans):
    def first(name):
        values = [s.duration_ms for s in spans if s.name == name and s.outcome == "ok"]
        return min(values) if values else None
    formation = [s for s in spans if s.name in {"resolve_client", "invoice", "product", "payment_document", "payment_product", "public_link"} and s.outcome != "skipped"]
    return dict(id=row.id, created_at=row.created_at, employee=row.employee_display_name or row.employee_external_id,
        phone=row.phone_normalized, status=row.status, queue_ms=first("initial_queue"), link_ms=first("link_ready"),
        formation_ms=sum(s.duration_ms for s in formation) if formation else None,
        qr_ms=first("browser_qr"), human_ms=sum(s.duration_ms for s in spans if s.name == "human_wait"),
        rest_calls=sum(s.kind == "rest" for s in spans),
        rest_retries=sum(s.kind == "rest" and s.details.get("attempt", 1) > 1 for s in spans),
        worker_retries=row.retry_count,
        browser_flags=[s.details for s in spans if s.name == "browser_qr"],
        measured=bool(spans))


@router.get("/api/admin/payment-timings")
async def timing_list(request: Request, date_from: str | None = None, date_to: str | None = None,
                      phone: str | None = None, employee: str | None = None, status: str | None = None,
                      page: int = 1, page_size: int = 50, admin=Depends(require_admin)):
    if page < 1 or not 1 <= page_size <= 100:
        raise ApiError(422, "VALIDATION_ERROR", "Некорректная пагинация")
    try:
        start, end = _date_bounds(date_from, date_to)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "Некорректные даты") from exc
    rows, spans, total, stats = await request.app.state.payment_repository.timing_list(
        date_from=start, date_to=end, phone=phone, employee=employee, status=status, page=page, page_size=page_size)
    grouped = {}
    for item in spans:
        grouped.setdefault(item.transaction_id, []).append(item)
    audit(logger, "payment.admin.timings.viewed", user_id=str(admin[2].user_id), count=total)
    return dict(items=[summary(row, grouped.get(row.id, [])) for row in rows], total=total, page=page, page_size=page_size, stats=stats)


@router.get("/api/admin/payment-timings/{transaction_id}")
async def timing_detail(transaction_id: UUID, request: Request, admin=Depends(require_admin)):
    repo = request.app.state.payment_repository
    row = await repo.get(transaction_id)
    if row is None:
        raise ApiError(404, "PAYMENT_NOT_FOUND", "Операция не найдена")
    spans = await repo.timing_detail(transaction_id)
    audit(logger, "payment.admin.timing.viewed", user_id=str(admin[2].user_id), transaction_id=str(transaction_id))
    return dict(**summary(row, spans), spans=[dict(id=s.id, parent_id=s.parent_id, run_id=s.run_id,
        name=s.name, kind=s.kind, started_at=s.started_at, duration_ms=s.duration_ms, outcome=s.outcome, details=s.details) for s in spans])
