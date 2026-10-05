from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.dependencies import require_gis_csrf, require_gis_data_session
from app.config import Settings, get_settings
from app.routers.gis import router
from app.schemas import SessionData, UserProfile


def jpeg_bytes() -> bytes:
    output = BytesIO()
    Image.new('RGB', (2, 2), color='white').save(output, format='JPEG')
    return output.getvalue()


class CapturingGisClient:
    def __init__(self) -> None:
        self.metadata = None
        self.photos = None
        self.feature_calls = []

    async def features(self, map_id, *, bbox, layers):
        self.feature_calls.append((map_id, bbox, layers))
        return {
            'type': 'FeatureCollection', 'truncated': False, 'limit': 100,
            'features': [
                {'type': 'Feature', 'id': 'point', 'geometry': {'type': 'Point', 'coordinates': [37.2, 55.1]}},
                {'type': 'Feature', 'id': 'point-2', 'geometry': {'type': 'Point', 'coordinates': [37.21, 55.11]}},
                {'type': 'Feature', 'id': 'line', 'geometry': {'type': 'LineString', 'coordinates': [[37.2, 55.1], [37.3, 55.2]]}},
                {'type': 'Feature', 'id': 'polygon', 'geometry': {'type': 'Polygon', 'coordinates': [[[37.2, 55.1], [37.3, 55.2], [37.2, 55.1]]]}},
            ],
        }

    async def create_report(self, metadata, photos):
        self.metadata = metadata
        self.photos = photos
        return {'id': 'report-id', 'external_report_id': metadata['external_report_id'], 'repeated': False, 'feature': {'closureFull': metadata['closureFull'], 'iconColor': '#ff5252' if metadata['closureFull'] else '#9c27b0'}}



def test_map_report_uses_server_author_without_connection_ticket():
    app = FastAPI()
    gis = CapturingGisClient()
    app.state.gis_client = gis
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Иван', last_name='Иванов', status='active'),
        csrf_token='csrf',
        created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_gis_csrf] = csrf_override
    feature_id, external_report_id, completion_id = uuid4(), uuid4(), uuid4()
    response = TestClient(app).post('/api/gis/reports', data={
        'feature_id': str(feature_id),
        'external_report_id': str(external_report_id),
        'completion_id': str(completion_id),
        'text': '  Проверили линию  ',
    }, files={'photos': ('work.jpg', jpeg_bytes(), 'image/jpeg')})

    assert response.status_code == 200
    assert gis.metadata == {
        'external_report_id': str(external_report_id),
        'completion_id': str(completion_id),
        'feature_id': str(feature_id),
        'technician': {'id': '17', 'name': 'Иван', 'last_name': 'Иванов'},
        'occurred_at': gis.metadata['occurred_at'],
        'text': 'Проверили линию',
        'closureFull': False,
        'fromScratch': False,
    }
    assert len(gis.photos) == 1
    assert gis.photos[0][0] == 'work.jpg'
    assert gis.photos[0][2] == 'image/jpeg'
    assert gis.photos[0][1].startswith(b'\xff\xd8\xff')


def test_features_keeps_only_points_and_lines():
    app = FastAPI()
    gis = CapturingGisClient()
    app.state.gis_client = gis
    app.include_router(router)

    async def session_override():
        return 'session', None

    app.dependency_overrides[require_gis_data_session] = session_override
    map_id = uuid4()
    response = TestClient(app).get(f'/api/gis/maps/{map_id}/features', params={
        'bbox': '37.1,55.0,37.4,55.3', 'layers': 'layer-1',
    })

    assert response.status_code == 200
    assert [feature['id'] for feature in response.json()['features']] == ['point', 'point-2', 'line']
    assert gis.feature_calls == [(str(map_id), '37.1,55.0,37.4,55.3', 'layer-1')]


def test_features_marks_points_noninteractive_below_detail_zoom():
    app = FastAPI()
    gis = CapturingGisClient()
    app.state.gis_client = gis
    app.include_router(router)

    async def session_override():
        return 'session', None

    app.dependency_overrides[require_gis_data_session] = session_override
    map_id = uuid4()
    response = TestClient(app).get(f'/api/gis/maps/{map_id}/features', params={
        'bbox': '37.1,55.0,37.4,55.3', 'layers': 'layer-1', 'zoom': 14,
    })

    assert response.status_code == 200
    features = response.json()['features']
    assert [feature['id'] for feature in features] == ['point', 'point-2', 'line']
    assert [feature['properties']['interactive'] for feature in features if feature['geometry']['type'] == 'Point'] == [False, False]


def test_features_limits_points_at_low_zoom_when_configured():
    app = FastAPI()
    gis = CapturingGisClient()
    app.state.gis_client = gis
    app.include_router(router)

    async def session_override():
        return 'session', None

    app.dependency_overrides[require_gis_data_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(gis_max_point_count=1)
    response = TestClient(app).get(f'/api/gis/maps/{uuid4()}/features', params={
        'bbox': '37.1,55.0,37.4,55.3', 'layers': 'layer-1', 'zoom': 15,
    })

    assert [feature['id'] for feature in response.json()['features']] == ['point', 'line']
    assert response.json()['truncated'] is True


def test_basemap_returns_official_sdk_url_only_when_key_is_configured():
    app = FastAPI()
    app.include_router(router)

    async def session_override():
        return 'session', None

    app.dependency_overrides[require_gis_data_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(yandex_maps_api_key='test-key')
    response = TestClient(app).get('/api/gis/basemap')

    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == {'provider': 'yandex', 'scriptUrl': 'https://api-maps.yandex.ru/2.1/?apikey=test-key&lang=ru_RU&csp=true', 'pointIconSize': 32, 'pointCircleSize': 22, 'pointFixedSizeMaxZoom': 15}

    app.dependency_overrides[get_settings] = lambda: Settings()
    assert TestClient(app).get('/api/gis/basemap').json() == {'provider': 'yandex', 'scriptUrl': None, 'pointIconSize': 32, 'pointCircleSize': 22, 'pointFixedSizeMaxZoom': 15}

    app.dependency_overrides[get_settings] = lambda: Settings(gis_map_provider='yandex-v3', yandex_maps_api_key='v3-key')
    assert TestClient(app).get('/api/gis/basemap').json() == {
        'provider': 'yandex-v3',
        'scriptUrl': 'https://api-maps.yandex.ru/v3/?apikey=v3-key&lang=ru_RU',
        'pointIconSize': 32,
        'pointCircleSize': 22,
        'pointFixedSizeMaxZoom': 15,
    }


def report_test_app(gis):
    app = FastAPI()
    app.state.gis_client = gis
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', status='active'),
        csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    app.dependency_overrides[require_gis_csrf] = lambda: ('session', session)
    from app.errors import ApiError
    from fastapi.responses import JSONResponse

    @app.exception_handler(ApiError)
    async def api_error(_request, error):
        return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status_code)

    return TestClient(app)


def test_map_report_passes_marks_timestamp_and_feature_receipt():
    gis = CapturingGisClient()
    client = report_test_app(gis)
    data = {
        'feature_id': str(uuid4()), 'external_report_id': str(uuid4()),
        'completion_id': str(uuid4()), 'text': 'Сварили муфту',
        'closureFull': 'true', 'fromScratch': 'true',
        'occurred_at': '2026-10-05T10:00:00Z',
    }
    for _ in range(2):
        response = client.post('/api/gis/reports', data=data)
        assert response.status_code == 200
        assert response.json()['feature'] == {'closureFull': True, 'iconColor': '#ff5252'}
        assert gis.metadata['closureFull'] is True
        assert gis.metadata['fromScratch'] is True
        assert gis.metadata['occurred_at'] == data['occurred_at']
        assert 'ticket_id' not in gis.metadata


def test_map_report_marks_do_not_replace_text_or_photos():
    gis = CapturingGisClient()
    response = report_test_app(gis).post('/api/gis/reports', data={
        'feature_id': str(uuid4()), 'external_report_id': str(uuid4()),
        'completion_id': str(uuid4()), 'text': '   ',
        'closureFull': 'true', 'fromScratch': 'true',
    })
    assert response.status_code == 422
    assert response.json()['code'] == 'GIS_REPORT_EMPTY'
    assert gis.metadata is None


def test_map_report_forwards_mark_point_only_error():
    from app.errors import ApiError

    class LineGisClient:
        async def create_report(self, metadata, photos):
            raise ApiError(400, 'MARK_POINT_ONLY', 'Отметки доступны только для точек')

    response = report_test_app(LineGisClient()).post('/api/gis/reports', data={
        'feature_id': str(uuid4()), 'external_report_id': str(uuid4()),
        'completion_id': str(uuid4()), 'text': 'Работы', 'closureFull': 'true',
    })
    assert response.status_code == 400
    assert response.json()['code'] == 'MARK_POINT_ONLY'


def test_feature_details_forwards_closure_full():
    class PointGisClient:
        async def feature(self, feature_id):
            return {'id': feature_id, 'closureFull': True}

    client = report_test_app(PointGisClient())
    client.app.dependency_overrides[require_gis_data_session] = lambda: ('session', None)
    feature_id = str(uuid4())
    assert client.get('/api/gis/features/' + feature_id).json() == {'id': feature_id, 'closureFull': True}
