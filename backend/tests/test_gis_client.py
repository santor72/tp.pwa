import httpx
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
