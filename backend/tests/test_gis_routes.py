from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.dependencies import require_gis_csrf
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
