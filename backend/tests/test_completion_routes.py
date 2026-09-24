from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from app.dependencies import require_csrf
from app.errors import ApiError
from app.routers.completions import router
from app.schemas import SessionData, UserProfile


class FakeCompletionService:
    def __init__(self) -> None:
        self.begin_args = None
        self.photos_available = False
        self.gis_photos_available = False

    async def begin(self, actor, **values):
        self.begin_args = (actor, values)
        return SimpleNamespace(id=uuid4())

    async def mark_techportal(self, operation_id):
        return SimpleNamespace(
            id=operation_id, ticket_id=32412, completion_status='completed', gis_status='not_requested',
            gis_report_id=None, last_error_code=None, last_error_message=None,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )


async def api_error(_, error: ApiError):
    return JSONResponse(status_code=error.status_code, content={'detail': error.message})


def test_connection_completion_requires_csrf_and_passes_server_context():
    app = FastAPI()
    completion = FakeCompletionService()
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Иван', last_name='Иванов', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
        upstream_cookies={'tp-session': 'employee'},
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    key = uuid4()
    response = TestClient(app).post('/api/tickets/32412/connection-completion', data={
        'day': 'today', 'idempotency_key': str(key), 'techportal_text': '  Подключили  ',
    })

    assert response.status_code == 200
    actor, values = completion.begin_args
    assert actor.techportal_user_id == '17'
    assert values == {
        'ticket_id': 32412, 'ticket_kind': 'connection', 'day': 'today', 'idempotency_key': key, 'techportal_text': 'Подключили', 'gis_text': '',
        'feature_id': None, 'photos': [], 'technician_name': 'Иван', 'technician_last_name': 'Иванов',
        'upstream_cookies': {'tp-session': 'employee'},
    }


def test_repair_completion_requires_text_and_passes_ticket_kind():
    app = FastAPI()
    completion = FakeCompletionService()
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Иван', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    client = TestClient(app)
    empty = client.post('/api/tickets/32412/ticket-completion', data={
        'day': 'today', 'ticket_kind': 'repair', 'idempotency_key': str(uuid4()), 'techportal_text': ' ',
    })
    assert empty.status_code == 422
    assert empty.json()['detail'] == 'Опишите выполненные работы'
    assert completion.begin_args is None

    complete = client.post('/api/tickets/32412/ticket-completion', data={
        'day': 'today', 'ticket_kind': 'repair', 'idempotency_key': str(uuid4()),
        'techportal_text': 'Заменили кабель',
    })
    assert complete.status_code == 200
    assert completion.begin_args[1]['ticket_kind'] == 'repair'
    assert completion.begin_args[1]['techportal_text'] == 'Заменили кабель'


def test_connection_completion_accepts_empty_report():
    app = FastAPI()
    completion = FakeCompletionService()
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Монтажник', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    response = TestClient(app).post('/api/tickets/32412/connection-completion', data={
        'day': 'today', 'idempotency_key': str(uuid4()), 'techportal_text': ' ',
    })
    assert response.status_code == 200
    assert completion.begin_args[1]['techportal_text'] == ''
    assert completion.begin_args[1]['photos'] == []


def test_connection_completion_rejects_photos_when_no_adapter_is_configured():
    app = FastAPI()
    completion = FakeCompletionService()
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Монтажник', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    response = TestClient(app).post('/api/tickets/32412/connection-completion', data={
        'day': 'today', 'idempotency_key': str(uuid4()), 'techportal_text': 'Подключили',
    }, files={'photos': ('work.jpg', b'not-validated-when-storage-is-off', 'image/jpeg')})

    assert response.status_code == 503
    assert response.json()['detail'] == 'Загрузка фотографий для заявок не настроена'
    assert completion.begin_args is None


def test_connection_completion_requires_s3_for_gis_photos():
    app = FastAPI()
    completion = FakeCompletionService()
    completion.photos_available = True
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Монтажник', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    response = TestClient(app).post('/api/tickets/32412/connection-completion', data={
        'day': 'today', 'idempotency_key': str(uuid4()), 'techportal_text': 'Подключили',
        'feature_id': str(uuid4()),
    }, files={'photos': ('work.jpg', b'not-read-when-s3-is-off', 'image/jpeg')})

    assert response.status_code == 503
    assert response.json()['detail'] == 'Для отправки фотографий в GIS требуется настроенное S3-хранилище'
    assert completion.begin_args is None


def test_connection_completion_accepts_gis_only_photo_with_techportal_text():
    app = FastAPI()
    completion = FakeCompletionService()
    completion.gis_photos_available = True
    app.state.connection_completion_service = completion
    app.add_exception_handler(ApiError, api_error)
    app.include_router(router)
    session = SessionData(
        user=UserProfile(id=17, email='tech@example.test', first_name='Монтажник', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def csrf_override():
        return 'session', session

    app.dependency_overrides[require_csrf] = csrf_override
    from app.config import Settings, get_settings
    app.dependency_overrides[get_settings] = lambda: Settings(gis_tikets_visible_techportal_roles='active')
    image = BytesIO()
    Image.new('RGB', (1, 1)).save(image, format='JPEG')
    response = TestClient(app).post('/api/tickets/32412/connection-completion', data={
        'day': 'today', 'idempotency_key': str(uuid4()), 'techportal_text': 'Подключили',
        'feature_id': str(uuid4()),
    }, files={'photos': ('work.jpg', image.getvalue(), 'image/jpeg')})

    assert response.status_code == 200
    assert completion.begin_args[1]['photos'][0]['content'] == image.getvalue()
