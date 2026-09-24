import logging
import json
import os
import time
import zlib
from pathlib import Path
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

    async def asset(self, asset_id: str, color: str | None = None) -> bytes:
        path = Path(self._settings.gis_icon_cache_dir) / f'{asset_id}.png'
        try:
            if path.is_file() and time.time() - path.stat().st_mtime < self._settings.gis_icon_cache_ttl_seconds:
                source = path.read_bytes()
                return self._recolor_icon(source, color) if color else source
        except OSError:
            pass
        token = self._settings.gis_api_token.get_secret_value()
        if not self._settings.gis_base_url or not token:
            raise ApiError(503, 'GIS_NOT_CONFIGURED', 'Интеграция с картой не настроена')
        try:
            response = await self._client.get(f'{self._settings.gis_base_url}/integration/v1/assets/{asset_id}', headers={'Authorization': f'Bearer {token}', 'Accept': 'image/png'})
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError('Карта временно недоступна') from exc
        if response.is_error:
            self._response(response, 'asset')
        if not response.headers.get('content-type', '').lower().startswith('image/png') or not response.content.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ApiError(502, 'GIS_RESPONSE_INVALID', 'Карта вернула некорректный значок')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(response.content)
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning('GIS icon cache unavailable', extra={'event': 'gis.icon_cache.unavailable', 'fields': {'error': type(exc).__name__}})
        return self._recolor_icon(response.content, color) if color else response.content

    @staticmethod
    def _recolor_icon(source: bytes, color: str) -> bytes:
        if not color or len(color) != 6 or any(c not in '0123456789abcdefABCDEF' for c in color):
            raise ApiError(422, 'GIS_ICON_COLOR_INVALID', 'Некорректный цвет значка')
        if len(source) < 33 or source[:8] != b'\x89PNG\r\n\x1a\n' or source[12:16] != b'IHDR' or source[25] != 3:
            raise ApiError(422, 'GIS_ICON_RECOLOR_UNSUPPORTED', 'Этот формат значка не поддерживает перекрашивание')
        offset = 8; palette = None; alpha = b''
        while offset + 12 <= len(source):
            size = int.from_bytes(source[offset:offset + 4], 'big'); end = offset + size + 12
            if end > len(source): break
            kind = source[offset + 4:offset + 8]
            if kind == b'PLTE': palette = offset
            elif kind == b'tRNS': alpha = source[offset + 8:offset + 8 + size]
            elif kind == b'IEND': break
            offset = end
        if palette is None:
            raise ApiError(422, 'GIS_ICON_RECOLOR_UNSUPPORTED', 'Этот формат значка не поддерживает перекрашивание')
        length = int.from_bytes(source[palette:palette + 4], 'big'); start = palette + 8
        entries = [source[start + i:start + i + 3] for i in range(0, length, 3)]
        base = max((rgb for index, rgb in enumerate(entries) if (alpha[index] if index < len(alpha) else 255) >= 200), key=lambda rgb: sum((255 - value) ** 2 for value in rgb), default=b'\xff\xff\xff')
        distance = sum((255 - value) ** 2 for value in base)
        if not distance: return source
        target = bytes.fromhex(color); output = bytearray(source)
        for index, rgb in enumerate(entries):
            if (alpha[index] if index < len(alpha) else 255) == 0: continue
            weight = min(1, sum((255 - rgb[channel]) * (255 - base[channel]) for channel in range(3)) / distance)
            for channel in range(3): output[start + index * 3 + channel] = round(255 + weight * (target[channel] - 255))
        output[start + length:start + length + 4] = zlib.crc32(output[palette + 4:start + length]).to_bytes(4, 'big')
        return bytes(output)

    async def report_by_external_id(self, external_report_id: str) -> dict[str, Any] | None:
        try:
            return await self._request('GET', f'reports/by-external-id/{external_report_id}')
        except ApiError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def create_report(self, metadata: dict[str, Any], photos: list[tuple[str, bytes, str]]) -> dict[str, Any]:
        token = self._settings.gis_api_token.get_secret_value()
        if not self._settings.gis_base_url or not token:
            raise ApiError(503, 'GIS_NOT_CONFIGURED', 'Интеграция с картой не настроена')
        try:
            response = await self._client.post(
                f'{self._settings.gis_base_url}/integration/v1/reports',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                data={'metadata': json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))},
                files=[('photos', (name, content, media_type)) for name, content, media_type in photos],
            )
        except httpx.HTTPError as exc:
            logger.warning('GIS unavailable', extra={'event': 'gis.unavailable', 'fields': {'operation': 'reports', 'error': type(exc).__name__}})
            raise ServiceUnavailableError('Карта временно недоступна') from exc
        return self._response(response, 'reports')

    async def _request(self, method: str, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        token = self._settings.gis_api_token.get_secret_value()
        if not self._settings.gis_base_url or not token:
            raise ApiError(503, 'GIS_NOT_CONFIGURED', 'Интеграция с картой не настроена')
        try:
            response = await self._client.request(method, f'{self._settings.gis_base_url}/integration/v1/{path.lstrip("/")}', headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'}, params=params)
        except httpx.HTTPError as exc:
            logger.warning('GIS unavailable', extra={'event': 'gis.unavailable', 'fields': {'operation': path, 'error': type(exc).__name__}})
            raise ServiceUnavailableError('Карта временно недоступна') from exc
        return self._response(response, path)

    def _response(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.status_code == 401:
            logger.error('GIS token rejected', extra={'event': 'gis.auth.failed', 'fields': {'operation': operation}})
            raise ApiError(502, 'GIS_AUTH_FAILED', 'Ошибка системной авторизации карты')
        if response.status_code in {400, 404, 409, 422}:
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
