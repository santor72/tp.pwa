import logging
import asyncio
import time
import uuid
from datetime import UTC, datetime
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings, get_settings
from app.capabilities import capabilities_for
from app.dependencies import actor_from_session, get_session_store, require_csrf, require_session, verify_origin
from app.errors import ApiError, PermissionDeniedError, SessionExpiredError
from app.logging import audit, configure_logging, request_id_ctx, stable_hash
from app.roles import UserRole
from app.schemas import (
    DialRequest,
    ErrorResponse,
    LoginRequest,
    SessionResponse,
    TicketCompletionRequest,
    TicketFiltersResponse,
    TicketResponse,
)
from app.session_store import SessionStore
from app.services import create_application_services
from app.routers.messengers import router as messengers_router
from app.routers.payments import router as payments_router
from app.routers.payment_timings import router as payment_timings_router
from app.routers.gis import router as gis_router
from app.routers.completions import router as completions_router
from app.payment_telemetry import Trace, current_trace, span
from app.payment_runtime_registry import PaymentRuntimeRegistry

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
MAX_CONNECTION_COMPLETION_BODY_BYTES = 51 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_redis = Redis.from_url(settings.session_redis_url, decode_responses=True)
    cache_redis = Redis.from_url(settings.cache_redis_url, decode_responses=True)
    app.state.session_redis = session_redis
    app.state.cache_redis = cache_redis
    app.state.session_store = SessionStore(session_redis, settings)
    app.state.services = create_application_services(settings, cache_redis)
    app.state.auth_provider = app.state.services.auth_provider
    app.state.payment_addresses = app.state.services.payment_addresses
    app.state.payment_catalog = app.state.services.payment_catalog
    app.state.payment_repository = app.state.services.payment_repository
    app.state.payment_service = app.state.services.payment_service
    app.state.payment_status = app.state.services.payment_status
    app.state.ticket_service = app.state.services.ticket_service
    app.state.messenger_links = app.state.services.messenger_links
    app.state.gis_client = app.state.services.gis_client
    app.state.connection_completion_service = app.state.services.connection_completion_service
    registry = PaymentRuntimeRegistry(app.state.services.sessions, settings.payment_processing_mode)
    app.state.payment_runtime_registry = registry
    owner = 'api:' + uuid.uuid4().hex
    stop = asyncio.Event()
    await registry.register(owner, 'api')
    heartbeat = asyncio.create_task(registry.heartbeat(owner, 'api', stop))
    try:
        yield
    finally:
        stop.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await registry.unregister(owner)
        finally:
            await app.state.services.close()
            await session_redis.aclose()
            await cache_redis.aclose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(messengers_router)
app.include_router(payments_router)
app.include_router(payment_timings_router)
app.include_router(gis_router)
app.include_router(completions_router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()
    trace = Trace() if settings.payment_telemetry_enabled and request.method == "POST" and request.url.path == "/api/payments" else None
    trace_token = current_trace.set(trace) if trace else None
    if trace is not None:
        request.state.timing_started_at = datetime.now(UTC)
        request.state.timing_started = started
    try:
        if request.method == 'POST' and request.url.path.endswith('/connection-completion'):
            try:
                content_length = int(request.headers.get('content-length', '0'))
            except ValueError:
                raise ApiError(422, 'REPORT_SIZE_LIMIT', 'Размер отчёта превышает допустимый лимит')
            if content_length > MAX_CONNECTION_COMPLETION_BODY_BYTES:
                raise ApiError(422, 'REPORT_SIZE_LIMIT', 'Размер отчёта превышает допустимый лимит')
        with span("http_accept", "operation"):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        audit(
            logger,
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            http_status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return response
    finally:
        if trace_token is not None:
            current_trace.reset(trace_token)
            await trace.save(getattr(request.app.state, "payment_repository", None))
        request_id_ctx.reset(token)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    payload = ErrorResponse(code=exc.code, message=exc.message, request_id=request_id_ctx.get())
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    payload = ErrorResponse(
        code="VALIDATION_ERROR",
        message="Проверьте заполнение обязательных полей",
        request_id=request_id_ctx.get(),
    )
    return JSONResponse(status_code=422, content=payload.model_dump())


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Необработанная ошибка", extra={"event": "internal.error", "fields": {"error": type(exc).__name__}})
    payload = ErrorResponse(code="INTERNAL_ERROR", message="Внутренняя ошибка сервиса", request_id=request_id_ctx.get())
    return JSONResponse(status_code=500, content=payload.model_dump())


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.session_absolute_ttl_seconds,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/", httponly=True, secure=settings.session_cookie_secure, samesite="strict")


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request) -> dict[str, str]:
    try:
        registry = getattr(request.app.state, 'payment_runtime_registry', None)
        if registry is not None and not await registry.healthy('api'):
            raise ApiError(503, 'PAYMENT_RUNTIME_UNHEALTHY', 'Обработчик платежей недоступен')
        await request.app.state.session_redis.ping()
        await request.app.state.cache_redis.ping()
        async with request.app.state.services.engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")
    except RedisError as exc:
        raise ApiError(503, "REDIS_UNAVAILABLE", "Redis недоступен") from exc
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=SessionResponse)
async def login(payload: LoginRequest, response: Response, request: Request) -> SessionResponse:
    verify_origin(request, settings)
    try:
        authenticated = await request.app.state.auth_provider.authenticate(payload.email, payload.password)
    except ApiError as exc:
        audit(logger, "auth.login.failed", result="failure", login_hash=stable_hash(payload.email), code=exc.code)
        raise
    persistent_user = await request.app.state.messenger_links.upsert_user(authenticated.user)
    session_id, session = await get_session_store(request).create(
        authenticated.user,
        persistent_user.id,
        authenticated.cookies,
    )
    set_session_cookie(response, session_id)
    audit(logger, "auth.login.succeeded", user_id=authenticated.user.id, result="success", session_hash=stable_hash(session_id))
    return SessionResponse(payment_telemetry_enabled=settings.payment_telemetry_enabled, user=authenticated.user, csrf_token=session.csrf_token, capabilities=capabilities_for(authenticated.user.role, authenticated.user.user_permissions, settings.messenger_show, techportal_status=authenticated.user.status, gis_visible_techportal_roles=settings.gis_visible_techportal_roles, connection_photos=request.app.state.connection_completion_service.photos_available, gis_photos=request.app.state.connection_completion_service.gis_photos_available))


@app.get("/api/auth/session", response_model=SessionResponse)
async def get_session(
    request: Request,
    session_pair: tuple[str, object] = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    _, session = session_pair
    return SessionResponse(
        user=session.user,
        payment_telemetry_enabled=settings.payment_telemetry_enabled,
        csrf_token=session.csrf_token,
        capabilities=capabilities_for(session.user.role, session.user.user_permissions, settings.messenger_show, techportal_status=session.user.status, gis_visible_techportal_roles=settings.gis_visible_techportal_roles, connection_photos=request.app.state.connection_completion_service.photos_available, gis_photos=request.app.state.connection_completion_service.gis_photos_available),
    )


@app.post("/api/auth/logout", status_code=204)
async def logout(
    session_pair: tuple[str, object] = Depends(require_csrf),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    session_id, session = session_pair
    await store.delete(session_id)
    response = Response(status_code=204)
    clear_session_cookie(response)
    audit(logger, "auth.logout", user_id=session.user.id, result="success", session_hash=stable_hash(session_id))
    return response


def ticket_filter_ids(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if len(values) > 100:
        raise ApiError(422, "VALIDATION_ERROR", "Слишком много значений фильтра")
    return values


@app.get("/api/tickets/filters", response_model=TicketFiltersResponse)
async def ticket_filters(
    request: Request,
    session_pair: tuple[str, object] = Depends(require_session),
) -> TicketFiltersResponse:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    if actor.role not in {UserRole.ADMIN, UserRole.MANAGER}:
        raise PermissionDeniedError()
    return await request.app.state.ticket_service.filters()


@app.get("/api/tickets/{day}", response_model=list[TicketResponse])
async def tickets_for_day(
    day: Literal["today", "tomorrow"],
    request: Request,
    session_pair: tuple[str, object] = Depends(require_session),
    scope: Literal["assigned", "all"] = "assigned",
    brigade_ids: str | None = None,
    master_ids: str | None = None,
) -> list[TicketResponse]:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    if scope == "all" and actor.role not in {UserRole.ADMIN, UserRole.MANAGER}:
        raise PermissionDeniedError()
    filter_brigade_ids = ticket_filter_ids(brigade_ids)
    filter_master_ids = ticket_filter_ids(master_ids)
    if scope != "all" and (filter_brigade_ids is not None or filter_master_ids is not None):
        raise ApiError(422, "VALIDATION_ERROR", "Фильтр доступен только в режиме всех заявок")
    try:
        result = await request.app.state.ticket_service.list_for_actor(
            actor, day, scope, filter_brigade_ids, filter_master_ids,
        )
    except ApiError as exc:
        audit(
            logger,
            "tickets.list.failed",
            user_id=str(actor.user_id), channel=actor.channel,
            day=day,
            scope=scope,
            brigade_count=len(filter_brigade_ids or []),
            master_count=len(filter_master_ids or []),
            result="failure",
            code=exc.code,
        )
        raise
    audit(
        logger,
        "tickets.list.succeeded",
        user_id=str(actor.user_id), channel=actor.channel,
        day=day,
        scope=scope,
        brigade_count=len(filter_brigade_ids or []),
        master_count=len(filter_master_ids or []),
        count=len(result),
        result="success",
    )
    return result


@app.post("/api/tickets/{ticket_id}/completion", response_model=TicketResponse)
async def set_ticket_completion(
    ticket_id: int,
    payload: TicketCompletionRequest,
    request: Request,
    session_pair: tuple[str, object] = Depends(require_csrf),
) -> TicketResponse:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    try:
        result = await request.app.state.ticket_service.set_completed_for_actor(
            actor,
            payload.day,
            ticket_id,
            payload.completed,
            payload.comment,
        )
    except ApiError as exc:
        audit(
            logger,
            "tickets.completion.failed",
            user_id=str(actor.user_id), channel=actor.channel,
            ticket_id=ticket_id,
            day=payload.day,
            result="failure",
            code=exc.code,
        )
        raise
    audit(
        logger,
        "tickets.completion.changed",
        user_id=str(actor.user_id), channel=actor.channel,
        ticket_id=ticket_id,
        day=payload.day,
        completed=payload.completed,
        result="success",
    )
    return result


@app.post("/api/conversations/dial", status_code=204)
async def dial_subscriber(
    payload: DialRequest,
    request: Request,
    session_pair: tuple[str, object] = Depends(require_csrf),
) -> Response:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    try:
        await request.app.state.ticket_service.dial(payload.phone, session.upstream_cookies)
    except ApiError as exc:
        audit(
            logger,
            "conversations.dial.failed",
            user_id=str(actor.user_id), channel=actor.channel,
            result="failure",
            code=exc.code,
        )
        raise
    audit(logger, "conversations.dial.succeeded", user_id=str(actor.user_id), channel=actor.channel, result="success")
    return Response(status_code=204)
