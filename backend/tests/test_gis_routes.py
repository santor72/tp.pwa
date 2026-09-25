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
        return {'id': 'report-id', 'external_report_id': metadata['external_report_id'], 'repeated': False, 'retention_until': None}



def test_map_report_uses_server_author_and_reserved_ticket_id():
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
        'ticket_id': 1,
        'completion_id': str(completion_id),
        'feature_id': str(feature_id),
        'technician': {'id': '17', 'name': 'Иван', 'last_name': 'Иванов'},
        'occurred_at': gis.metadata['occurred_at'],
        'text': 'Проверили линию',
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
    assert response.json() == {'provider': 'yandex', 'scriptUrl': 'https://api-maps.yandex.ru/2.1/?apikey=test-key&lang=ru_RU&csp=true'}

    app.dependency_overrides[get_settings] = lambda: Settings()
    assert TestClient(app).get('/api/gis/basemap').json() == {'provider': 'yandex', 'scriptUrl': None}
