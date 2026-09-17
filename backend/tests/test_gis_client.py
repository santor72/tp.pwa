import httpx
import json
import pytest

from app.config import Settings
from app.errors import ApiError
from app.gis_client import GisClient


def configured(**values):
    return Settings(gis_base_url='https://gis.test', gis_api_token='t' * 32, **values)


@pytest.mark.asyncio
async def test_gis_client_keeps_token_on_server_and_forwards_map_response():
    seen = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen['url'] = str(request.url)
        seen['authorization'] = request.headers.get('Authorization')
        return httpx.Response(200, json={'rows': [{'id': 'map'}]})

    client = GisClient(configured(), httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    assert await client.maps() == {'rows': [{'id': 'map'}]}
    assert seen == {'url': 'https://gis.test/integration/v1/maps', 'authorization': f'Bearer {"t" * 32}'}


@pytest.mark.asyncio
async def test_gis_client_hides_upstream_token_failure():
    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={'error': 'token details must not escape'})

    client = GisClient(configured(), httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    with pytest.raises(ApiError, match='системной авторизации') as error:
        await client.maps()
    assert error.value.code == 'GIS_AUTH_FAILED'


@pytest.mark.asyncio
async def test_gis_client_requires_configuration_before_request():
    client = GisClient(Settings(), httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    with pytest.raises(ApiError) as error:
        await client.maps()
    assert error.value.code == 'GIS_NOT_CONFIGURED'


@pytest.mark.asyncio
async def test_gis_client_sends_map_report_as_server_authenticated_multipart():
    seen = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen['authorization'] = request.headers.get('Authorization')
        seen['content_type'] = request.headers.get('Content-Type')
        seen['body'] = await request.aread()
        return httpx.Response(201, json={'id': 'report', 'external_report_id': 'external', 'repeated': False, 'retention_until': None})

    client = GisClient(configured(), httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    metadata = {'external_report_id': 'external', 'ticket_id': 1, 'text': 'Готово'}
    result = await client.create_report(metadata, [('work.jpg', b'photo', 'image/jpeg')])

    assert result['id'] == 'report'
    assert seen['authorization'] == f'Bearer {"t" * 32}'
    assert seen['content_type'].startswith('multipart/form-data; boundary=')
    assert b'name="metadata"' in seen['body']
    assert json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode() in seen['body']
    assert b'name="photos"; filename="work.jpg"' in seen['body']


@pytest.mark.asyncio
async def test_gis_client_returns_none_for_missing_external_report_and_receipt_when_present():
    requests = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith('/missing'):
            return httpx.Response(404, json={'code': 'GIS_REPORT_NOT_FOUND', 'error': 'not found'})
        return httpx.Response(200, json={'id': 'report-id', 'external_report_id': 'known'})

    client = GisClient(configured(), httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    assert await client.report_by_external_id('missing') is None
    assert await client.report_by_external_id('known') == {'id': 'report-id', 'external_report_id': 'known'}
    assert requests == [
        'https://gis.test/integration/v1/reports/by-external-id/missing',
        'https://gis.test/integration/v1/reports/by-external-id/known',
    ]
