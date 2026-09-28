import httpx

from app.config import Settings
from app.errors import ApiError, ServiceUnavailableError


class MobileMapClient:
    """Read-only service connection. Credentials never leave the backend."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=settings.mobilemap_timeout_seconds)
        self.owned = client is None

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def get(self, path: str, params: dict | None = None) -> httpx.Response:
        s = self.settings
        if not s.mobilemap_base_url or not s.mobilemap_username or not s.mobilemap_password.get_secret_value():
            raise ApiError(503, 'MOBILEMAP_NOT_CONFIGURED', 'Подключение mobilemap не настроено')
        try:
            response = await self.client.get(
                f'{s.mobilemap_base_url.rstrip("/")}/api/{path}', params=params,
                auth=httpx.BasicAuth(s.mobilemap_username, s.mobilemap_password.get_secret_value()),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError('Карта временно недоступна') from exc
        if response.status_code in (401, 403):
            raise ApiError(502, 'MOBILEMAP_AUTH_FAILED', 'Ошибка подключения к сервису карты')
        if response.status_code == 404:
            raise ApiError(404, 'MOBILEMAP_NOT_FOUND', 'Объект карты не найден')
        if response.status_code == 429 or response.is_server_error:
            raise ServiceUnavailableError('Карта временно недоступна')
        if not response.is_success:
            raise ApiError(502, 'MOBILEMAP_HTTP_ERROR', 'Сервис карты вернул ошибку')
        return response

    async def json(self, path: str, params: dict | None = None) -> dict:
        response = await self.get(path, params)
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError as exc:
            raise ApiError(502, 'MOBILEMAP_RESPONSE_INVALID', 'Некорректный ответ сервиса карты') from exc
