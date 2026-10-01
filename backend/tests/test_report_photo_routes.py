from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.dependencies import require_csrf, require_gis_csrf
from app.errors import ApiError
from app.routers.completions import router as completion_router
from app.routers.gis import router as gis_router
from app.schemas import SessionData, UserProfile


class PhotoReceiver:
    photos_available = True
    gis_photos_available = True

    def __init__(self):
        self.photos = None
        self.closed = False

    async def create_report(self, metadata, photos):
        self.photos = photos
        return {'id': 'report', 'repeated': False}

    async def begin(self, actor, **payload):
        self.photos = payload['photos']
        return SimpleNamespace(id=uuid4())

    async def mark_techportal(self, operation_id):
        self.closed = True
        return SimpleNamespace(
            id=operation_id, ticket_id=32412, completion_status='completed', gis_status='not_requested',
            gis_report_id=None, last_error_code=None, last_error_message=None,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )


@pytest.fixture(params=['/api/gis/reports', '/api/tickets/32412/connection-completion', '/api/tickets/32412/ticket-completion'])
def upload_route(request):
    app = FastAPI()
    receiver = PhotoReceiver()
    app.state.gis_client = receiver
    app.state.connection_completion_service = receiver
    app.include_router(gis_router)
    app.include_router(completion_router)

    async def csrf():
        return 'session', SessionData(
            user=UserProfile(id=17, email='tech@example.test', status='active'),
            internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
            absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def api_error(_, error):
        return JSONResponse(status_code=error.status_code, content={'code': error.code, 'message': error.message})

    app.dependency_overrides[require_csrf] = csrf
    app.dependency_overrides[require_gis_csrf] = csrf
    app.add_exception_handler(ApiError, api_error)
    data = {
        'feature_id': str(uuid4()), 'external_report_id': str(uuid4()), 'completion_id': str(uuid4()),
    } if request.param == '/api/gis/reports' else {
        'day': 'today', 'idempotency_key': str(uuid4()), 'techportal_text': 'Выполнено',
        'ticket_kind': 'repair' if request.param.endswith('/ticket-completion') else 'connection',
    }
    return TestClient(app), request.param, data, receiver


def test_route_delivers_a_jpeg_for_misnamed_heic_with_wrong_mime(upload_route):
    client, path, data, receiver = upload_route
    heic = (Path(__file__).parent / 'fixtures' / 'iphone.heic').read_bytes()
    response = client.post(path, data=data, files={'photos': ('phone.jpeg', heic, 'application/octet-stream')})
    assert response.status_code == 200
    photo = receiver.photos[0]
    name, content, mime = photo if isinstance(photo, tuple) else (photo['name'], photo['content'], photo['content_type'])
    assert name == 'phone.jpg'
    assert mime == 'image/jpeg'
    with Image.open(BytesIO(content)) as image:
        image.load()
        assert image.format == 'JPEG'
        assert image.size == (56, 56)


@pytest.mark.parametrize('content', [b'\xff\xd8\xffbroken', b'%PDF-1.7 fake.jpeg'])
def test_invalid_file_is_rejected_before_delivery_or_ticket_closure(upload_route, content):
    client, path, data, receiver = upload_route
    response = client.post(path, data=data, files={'photos': ('phone.jpeg', content, 'image/jpeg')})
    assert response.status_code == 422
    assert response.json()['message'].startswith('Фото 1:')
    assert receiver.photos is None
    assert not receiver.closed
