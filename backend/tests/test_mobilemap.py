import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import require_gis_data_session
from app.errors import ApiError
from app.mobilemap_client import MobileMapClient
from app.routers.mobilemap import router


def service(handler):
    return MobileMapClient(Settings(mobilemap_base_url='http://mobilemap-api:8000', mobilemap_username='pwa', mobilemap_password='secret'), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def application(client, authorized=True):
    app = FastAPI()
    app.state.mobilemap_client = client
    app.state.session_store = None
    app.include_router(router)
    @app.exception_handler(ApiError)
    async def error(_, exc):
        return JSONResponse({'code': exc.code}, status_code=exc.status_code)
    if authorized:
        app.dependency_overrides[require_gis_data_session] = lambda: ('session', None)
    return TestClient(app)


def test_transport_uses_basic_and_preserves_query_and_response():
    def handler(request):
        assert request.headers['authorization'] == 'Basic cHdhOnNlY3JldA=='
        assert request.url.params['zoom'] == '14'
        return httpx.Response(200, json={'features': [], 'thinned': True})
    client = service(handler)
    result = application(client).get(f'/api/mobilemap/maps/{uuid4()}/features', params={'bbox': '37,55,38,56', 'zoom': 14})
    assert result.status_code == 200
    assert result.json()['thinned'] is True
    assert result.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('status,expected', [(401,502), (403,502), (404,404), (500,503), (429,503), (302,502)])
def test_upstream_errors_do_not_expire_user_session(status, expected):
    client = service(lambda _: httpx.Response(status))
    assert application(client).get('/api/mobilemap/maps').status_code == expected


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout('timeout', request=request)
    assert application(service(handler)).get('/api/mobilemap/maps').status_code == 503


@pytest.mark.parametrize('path', ['/maps', '/config', '/source', f'/assets/{uuid4()}', f'/features/{uuid4()}', f'/maps/{uuid4()}/tiles/lines/network/v1/10/500/300.webp'])
def test_every_read_requires_session(path):
    def handler(_):
        pytest.fail('Unauthenticated request reached mobilemap')
    assert application(service(handler), authorized=False).get('/api/mobilemap' + path).status_code == 401


def test_tile_private_cache_revalidates_and_supports_retina():
    def handler(request):
        assert request.url.path.endswith('/network/v1/10/500/300@2x.webp')
        return httpx.Response(200, content=b'webp', headers={'Content-Type': 'image/webp', 'Cache-Control': 'public,max-age=31536000'})
    app = application(service(handler))
    path = f'/api/mobilemap/maps/{uuid4()}/tiles/lines/network/v1/10/500/300@2x.webp'
    response = app.get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-cache'
    assert app.get(path, headers={'If-None-Match': response.headers['etag']}).status_code == 304
    assert app.get(path.replace('/500/', '/1024/')).status_code == 404


def test_no_sync_or_arbitrary_proxy():
    def handler(_):
        pytest.fail('Unexpected upstream request')
    app = application(service(handler))
    assert app.post('/api/mobilemap/sync').status_code == 404
    assert app.get('/api/mobilemap/arbitrary').status_code == 404


def test_invalid_json_and_image_are_rejected():
    app = application(service(lambda _: httpx.Response(200, text='html')))
    assert app.get('/api/mobilemap/maps').status_code == 502
    assert app.get(f'/api/mobilemap/assets/{uuid4()}').status_code == 502


@pytest.mark.parametrize('role,map_roles,picker_roles,expected', [
    ('viewer', '', '', 403), ('installer', 'installer', '', 200), ('picker', '', 'picker', 200),
])
def test_role_permissions_cover_both_scenarios(role, map_roles, picker_roles, expected):
    from types import SimpleNamespace
    from app.dependencies import require_session
    client = application(service(lambda _: httpx.Response(200, json={'rows': []})), authorized=False)
    client.app.dependency_overrides[require_session] = lambda: ('s', SimpleNamespace(user=SimpleNamespace(status=role)))
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, gis_visible_techportal_roles=map_roles, gis_tikets_visible_techportal_roles=picker_roles)
    assert client.get('/api/mobilemap/maps').status_code == expected
