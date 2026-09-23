from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.capabilities import gis_visible_for_techportal_role
from app.config import Settings, get_settings
from app.dependencies import actor_from_session, require_csrf, require_session
from app.errors import ApiError
from app.report_photos import MAX_PHOTO_BYTES, validate_report_photo
from app.schemas import ConnectionCompletionResponse, SessionData

router = APIRouter(prefix='/api/tickets', tags=['ticket-completions'])


def response(operation) -> ConnectionCompletionResponse:
    return ConnectionCompletionResponse(
        id=operation.id, ticket_id=operation.ticket_id, completion_status=operation.completion_status,
        gis_status=operation.gis_status, gis_report_id=operation.gis_report_id,
        error_code=operation.last_error_code, error_message=operation.last_error_message,
        created_at=operation.created_at, updated_at=operation.updated_at,
    )


@router.post('/{ticket_id}/connection-completion', response_model=ConnectionCompletionResponse)
async def complete_connection(
    ticket_id: int,
    request: Request,
    day: str = Form(),
    idempotency_key: UUID = Form(),
    techportal_text: str = Form(default='', max_length=10_000),
    gis_text: str = Form(default='', max_length=10_000),
    feature_id: UUID | None = Form(default=None),
    photos: list[UploadFile] = File(default=[]),
    session_pair: tuple[str, SessionData] = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
):
    techportal_report_text = techportal_text.strip()
    gis_report_text = gis_text.strip()
    if day not in {'today', 'tomorrow'}:
        raise ApiError(422, 'VALIDATION_ERROR', 'Укажите день заявки')
    if len(photos) > 5:
        raise ApiError(422, 'REPORT_PHOTOS_LIMIT', 'В одном отчёте можно загрузить до 5 фотографий')
    completion_service = request.app.state.connection_completion_service
    if photos and not completion_service.photos_available and not feature_id:
        raise ApiError(503, 'PHOTO_STORAGE_NOT_CONFIGURED', 'Загрузка фотографий для заявок не настроена')
    if feature_id and photos and not completion_service.gis_photos_available:
        raise ApiError(503, 'GIS_PHOTO_STORAGE_NOT_CONFIGURED', 'Для отправки фотографий в GIS требуется настроенное S3-хранилище')
    if not techportal_report_text and not (photos and completion_service.photos_available):
        raise ApiError(422, 'CONNECTION_REPORT_REQUIRED', 'Добавьте текст для ТехПортала или фотографию')
    if feature_id and not gis_report_text and not photos:
        raise ApiError(422, 'GIS_REPORT_REQUIRED', 'Добавьте текст отчёта GIS или фотографию')
    payload_photos = []
    for photo in photos:
        content_type = photo.content_type or ''
        content = await photo.read(MAX_PHOTO_BYTES + 1)
        validate_report_photo(content_type, content)
        payload_photos.append({'name': photo.filename or 'photo', 'content_type': content_type, 'content': content})
    _, session = session_pair
    if feature_id and not gis_visible_for_techportal_role(session.user.status, settings.gis_visible_techportal_roles):
        raise ApiError(403, 'GIS_ACCESS_DENIED', 'Нет доступа к карте GIS')
    actor = await actor_from_session(request, session)
    technician_name = (session.user.first_name or session.user.email).strip() or str(session.user.id)
    technician_last_name = (session.user.last_name or '').strip()
    operation = await request.app.state.connection_completion_service.begin(
        actor, ticket_id=ticket_id, day=day, idempotency_key=idempotency_key,
        techportal_text=techportal_report_text, gis_text=gis_report_text, feature_id=feature_id,
        photos=payload_photos, technician_name=technician_name, technician_last_name=technician_last_name,
        upstream_cookies=session.upstream_cookies,
    )
    operation = await request.app.state.connection_completion_service.mark_techportal(operation.id)
    return response(operation)


@router.get('/connection-completions/{operation_id}', response_model=ConnectionCompletionResponse)
async def connection_completion_status(
    operation_id: UUID,
    request: Request,
    session_pair: tuple[str, SessionData] = Depends(require_session),
):
    _, session = session_pair
    actor = await actor_from_session(request, session)
    operation = await request.app.state.connection_completion_service.get_for_user(operation_id, actor.user_id)
    if operation is None:
        raise ApiError(404, 'COMPLETION_NOT_FOUND', 'Операция выполнения не найдена')
    return response(operation)
