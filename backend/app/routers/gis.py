from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.dependencies import require_gis_csrf, require_gis_session
from app.config import Settings, get_settings
from app.errors import ApiError
from app.report_photos import MAX_PHOTO_BYTES, validate_report_photo
from app.schemas import SessionData

router = APIRouter(prefix='/api/gis', tags=['gis'])
DRAWABLE_GEOMETRIES = {'Point', 'LineString', 'Polygon'}


@router.get('/basemap')
async def basemap(_: tuple[str, SessionData] = Depends(require_gis_session), settings: Settings = Depends(get_settings)):
    """Return active map-provider configuration only to GIS-enabled sessions."""
    key = settings.yandex_maps_api_key.get_secret_value().strip()
    script_url = f'https://api-maps.yandex.ru/2.1/?apikey={key}&lang=ru_RU&csp=true' if key else None
    return JSONResponse({'provider': settings.gis_map_provider, 'scriptUrl': script_url}, headers={'Cache-Control': 'no-store'})


@router.get('/maps')
async def maps(request: Request, _: tuple[str, SessionData] = Depends(require_gis_session)):
    return await request.app.state.gis_client.maps()


@router.get('/maps/{map_id}/layers')
async def layers(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_session)):
    return await request.app.state.gis_client.layers(str(map_id))


@router.get('/maps/{map_id}/bounds')
async def bounds(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_session)):
    return await request.app.state.gis_client.bounds(str(map_id))


@router.get('/maps/{map_id}/features')
async def features(map_id: UUID, request: Request, bbox: str = Query(pattern=r'^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$'), layers: str | None = Query(default=None, max_length=2048), _: tuple[str, SessionData] = Depends(require_gis_session)):
    result = await request.app.state.gis_client.features(str(map_id), bbox=bbox, layers=layers)
    features = result.get('features')
    if isinstance(features, list):
        result = {
            **result,
            'features': [
                feature for feature in features
                if isinstance(feature, dict)
                and isinstance(feature.get('geometry'), dict)
                and feature['geometry'].get('type') in DRAWABLE_GEOMETRIES
            ],
        }
    return result


@router.get('/maps/{map_id}/search')
async def search(map_id: UUID, request: Request, q: str = Query(min_length=1, max_length=200), _: tuple[str, SessionData] = Depends(require_gis_session)):
    return await request.app.state.gis_client.search(str(map_id), q)


@router.get('/features/{feature_id}')
async def feature(feature_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_session)):
    return await request.app.state.gis_client.feature(str(feature_id))

@router.get('/assets/{asset_id}')
async def asset(asset_id: UUID, request: Request, color: str | None = Query(default=None, pattern=r'^[a-fA-F0-9]{6}$'), _: tuple[str, SessionData] = Depends(require_gis_session)):
    return Response(await request.app.state.gis_client.asset(str(asset_id), color), media_type='image/png', headers={'Cache-Control': 'private, max-age=3600'})


@router.post('/reports')
async def create_report(
    request: Request,
    feature_id: UUID = Form(),
    external_report_id: UUID = Form(),
    completion_id: UUID = Form(),
    text: str = Form(default='', max_length=10_000),
    photos: list[UploadFile] = File(default=[]),
    session_pair: tuple[str, SessionData] = Depends(require_gis_csrf),
):
    """Create a manual map report. Ticket 1 is the agreed sentinel until map reports get their own GIS type."""
    report_text = text.strip()
    if len(photos) > 5:
        raise ApiError(422, 'GIS_REPORT_PHOTOS_LIMIT', 'В одном отчёте можно загрузить до 5 фотографий')
    if not report_text and not photos:
        raise ApiError(422, 'GIS_REPORT_EMPTY', 'Добавьте текст или фотографию')
    payload_photos: list[tuple[str, bytes, str]] = []
    for photo in photos:
        content = await photo.read(MAX_PHOTO_BYTES + 1)
        validate_report_photo(photo.content_type or '', content, code_prefix='GIS_REPORT')
        payload_photos.append((photo.filename or f'{uuid4()}.jpg', content, photo.content_type))
    session = session_pair[1]
    technician_name = (session.user.first_name or session.user.email).strip() or str(session.user.id)
    technician_last_name = (session.user.last_name or '').strip()
    metadata = {
        'external_report_id': str(external_report_id),
        'ticket_id': 1,
        'completion_id': str(completion_id),
        'feature_id': str(feature_id),
        'technician': {'id': str(session.user.id), 'name': technician_name, 'last_name': technician_last_name},
        'occurred_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'text': report_text,
    }
    return await request.app.state.gis_client.create_report(metadata, payload_photos)
