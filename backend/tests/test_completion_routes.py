from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.dependencies import require_csrf
from app.errors import ApiError
from app.routers.completions import router
from app.schemas import SessionData, UserProfile


class FakeCompletionService:
    def __init__(self) -> None:
        self.begin_args = None

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
        user=UserProfile(id=17, email='tech@example.test', first_name='Монтажник', status='active'),
        internal_user_id=uuid4(), csrf_token='csrf', created_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC) + timedelta(hours=1),
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
        'ticket_id': 32412, 'day': 'today', 'idempotency_key': key, 'techportal_text': 'Подключили', 'gis_text': '',
        'feature_id': None, 'photos': [], 'technician_name': 'Монтажник',
    }


def test_connection_completion_rejects_empty_report_before_service():
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
    assert response.status_code == 422
    assert response.json()['detail'] == 'Добавьте текст для ТехПортала или фотографию'
    assert completion.begin_args is None
