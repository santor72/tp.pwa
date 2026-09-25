from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.dependencies import require_gis_csrf, require_gis_data_session
from app.config import Settings, get_settings
from app.errors import ApiError
from app.report_photos import MAX_PHOTO_BYTES, validate_report_photo
from app.schemas import SessionData

router = APIRouter(prefix='/api/gis', tags=['gis'])
DRAWABLE_GEOMETRIES = {'Point', 'LineString'}
POINT_CLUSTER_GRID_SIZE = 24


def cluster_points(features: list[dict], bbox: str) -> list[dict]:
    west, south, east, north = (float(value) for value in bbox.split(','))
    width = max(east - west, 0.000001)
    height = max(north - south, 0.000001)
    groups: dict[tuple[str, int, int], list[dict]] = {}
    other: list[dict] = []
    for feature in features:
        geometry = feature.get('geometry')
        if not isinstance(geometry, dict) or geometry.get('type') != 'Point':
            other.append(feature)
            continue
        coordinates = geometry.get('coordinates')
        if not isinstance(coordinates, list) or len(coordinates) < 2 or not all(isinstance(value, (int, float)) for value in coordinates[:2]):
            continue
        properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
        cell_x = min(POINT_CLUSTER_GRID_SIZE - 1, max(0, int((coordinates[0] - west) / width * POINT_CLUSTER_GRID_SIZE)))
        cell_y = min(POINT_CLUSTER_GRID_SIZE - 1, max(0, int((coordinates[1] - south) / height * POINT_CLUSTER_GRID_SIZE)))
        groups.setdefault((str(properties.get('layer_id', '')), cell_x, cell_y), []).append(feature)
    clustered: list[dict] = []
    for (layer_id, cell_x, cell_y), items in groups.items():
        if len(items) == 1:
            clustered.extend(items)
            continue
        coordinates = [item['geometry']['coordinates'] for item in items]
        longitudes = [point[0] for point in coordinates]
        latitudes = [point[1] for point in coordinates]
        clustered.append({
            'type': 'Feature',
            'id': f'cluster:{layer_id}:{cell_x}:{cell_y}',
            'geometry': {'type': 'Point', 'coordinates': [sum(longitudes) / len(longitudes), sum(latitudes) / len(latitudes)]},
            'properties': {
                'id': f'cluster:{layer_id}:{cell_x}:{cell_y}', 'layer_id': layer_id, 'kind': 'cluster', 'cluster': True,
                'count': len(items), 'bbox': [min(longitudes), min(latitudes), max(longitudes), max(latitudes)],
            },
        })
    return other + clustered


@router.get('/basemap')
async def basemap(_: tuple[str, SessionData] = Depends(require_gis_data_session), settings: Settings = Depends(get_settings)):
    """Return active map-provider configuration only to GIS-enabled sessions."""
    key = settings.yandex_maps_api_key.get_secret_value().strip()
    script_url = f'https://api-maps.yandex.ru/2.1/?apikey={key}&lang=ru_RU&csp=true' if key else None
    return JSONResponse({'provider': settings.gis_map_provider, 'scriptUrl': script_url}, headers={'Cache-Control': 'no-store'})


@router.get('/maps')
async def maps(request: Request, _: tuple[str, SessionData] = Depends(require_gis_data_session)):
    return await request.app.state.gis_client.maps()


@router.get('/maps/{map_id}/layers')
async def layers(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_data_session)):
    return await request.app.state.gis_client.layers(str(map_id))


@router.get('/maps/{map_id}/bounds')
async def bounds(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_data_session)):
    return await request.app.state.gis_client.bounds(str(map_id))


@router.get('/maps/{map_id}/features')
async def features(map_id: UUID, request: Request, bbox: str = Query(pattern=r'^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$'), layers: str | None = Query(default=None, max_length=2048), zoom: int = Query(default=15, ge=1, le=23), _: tuple[str, SessionData] = Depends(require_gis_data_session), settings: Settings = Depends(get_settings)):
    result = await request.app.state.gis_client.features(str(map_id), bbox=bbox, layers=layers)
    features = result.get('features')
    if isinstance(features, list):
        visible_features = [
            feature for feature in features
            if isinstance(feature, dict)
            and isinstance(feature.get('geometry'), dict)
            and feature['geometry'].get('type') in DRAWABLE_GEOMETRIES
        ]
        result = {
            **result,
            'features': visible_features if zoom >= settings.gis_point_detail_zoom else cluster_points(visible_features, bbox),
        }
    return result


@router.get('/maps/{map_id}/search')
async def search(map_id: UUID, request: Request, q: str = Query(min_length=1, max_length=200), _: tuple[str, SessionData] = Depends(require_gis_data_session)):
    return await request.app.state.gis_client.search(str(map_id), q)


@router.get('/features/{feature_id}')
async def feature(feature_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_gis_data_session)):
    return await request.app.state.gis_client.feature(str(feature_id))

@router.get('/assets/{asset_id}')
async def asset(asset_id: UUID, request: Request, color: str | None = Query(default=None, pattern=r'^[a-fA-F0-9]{6}$'), _: tuple[str, SessionData] = Depends(require_gis_data_session)):
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
