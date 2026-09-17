import logging
from typing import Any

import httpx

from app.config import Settings
from app.errors import ApiError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class GisClient:
    """Narrow server-to-server client for the GIS integration API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.gis_timeout_seconds))
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def maps(self) -> dict[str, Any]: return await self._request('GET', 'maps')
    async def layers(self, map_id: str) -> dict[str, Any]: return await self._request('GET', f'maps/{map_id}/layers')
    async def bounds(self, map_id: str) -> dict[str, Any]: return await self._request('GET', f'maps/{map_id}/bounds')
    async def features(self, map_id: str, *, bbox: str, layers: str | None = None) -> dict[str, Any]:
        params = {'bbox': bbox, **({'layers': layers} if layers else {})}
        return await self._request('GET', f'maps/{map_id}/features', params=params)
    async def search(self, map_id: str, query: str) -> dict[str, Any]: return await self._request('GET', f'maps/{map_id}/search', params={'q': query})
    async def feature(self, feature_id: str) -> dict[str, Any]: return await self._request('GET', f'features/{feature_id}')

    async def _request(self, method: str, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        token = self._settings.gis_api_token.get_secret_value()
        if not self._settings.gis_base_url or not token:
            raise ApiError(503, 'GIS_NOT_CONFIGURED', 'Интеграция с картой не настроена')
        try:
            response = await self._client.request(method, f'{self._settings.gis_base_url}/integration/v1/{path.lstrip("/")}', headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'}, params=params)
        except httpx.HTTPError as exc:
            logger.warning('GIS unavailable', extra={'event': 'gis.unavailable', 'fields': {'operation': path, 'error': type(exc).__name__}})
            raise ServiceUnavailableError('Карта временно недоступна') from exc
        if response.status_code == 401:
            logger.error('GIS token rejected', extra={'event': 'gis.auth.failed', 'fields': {'operation': path}})
            raise ApiError(502, 'GIS_AUTH_FAILED', 'Ошибка системной авторизации карты')
        if response.status_code in {404, 422}:
            payload = self._json(response)
            raise ApiError(response.status_code, payload.get('code', 'GIS_NOT_FOUND'), payload.get('error', 'Объект карты не найден'))
        if response.status_code == 429 or response.is_server_error: raise ServiceUnavailableError('Карта временно недоступна')
        if response.is_error: raise ApiError(502, 'GIS_HTTP_ERROR', 'Карта вернула ошибку HTTP')
        return self._json(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try: data = response.json()
        except ValueError as exc: raise ApiError(502, 'GIS_RESPONSE_INVALID', 'Карта вернула некорректный ответ') from exc
        if not isinstance(data, dict): raise ApiError(502, 'GIS_RESPONSE_INVALID', 'Карта вернула некорректный ответ')
        return data
