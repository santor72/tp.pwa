import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.errors import AuthenticationError, ServiceUnavailableError
from app.schemas import UserProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: UserProfile
    cookies: dict[str, str]


class TechPortalAuthProvider:
    """Использует ТехПортал только для проверки учётных данных."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def authenticate(self, email: str, password: str) -> AuthenticatedUser:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self._settings.tp_origin_url}/login",
        }
        timeout = httpx.Timeout(self._settings.tp_auth_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                csrf_token = await self._get_csrf_token(client, headers)
                response = await self._login(client, headers, csrf_token, email, password)
                if response.status_code == 403:
                    csrf_token = await self._get_csrf_token(client, headers)
                    response = await self._login(client, headers, csrf_token, email, password)
        except httpx.HTTPError as exc:
            logger.warning("ТехПортал недоступен", extra={"event": "auth.provider.unavailable", "fields": {"error": type(exc).__name__}})
            raise ServiceUnavailableError("Сервис авторизации временно недоступен") from exc

        if response.status_code in {400, 401, 403}:
            raise AuthenticationError()
        if response.is_error:
            logger.warning(
                "Неожиданный ответ ТехПортала",
                extra={"event": "auth.provider.failed", "fields": {"upstream_status": response.status_code}},
            )
            raise ServiceUnavailableError("Сервис авторизации временно недоступен")

        try:
            payload: dict[str, Any] = response.json()
            properties = payload.get("properties")
            permissions = properties.get("permissions") if isinstance(properties, dict) else {}
            return AuthenticatedUser(
                user=UserProfile(
                    id=payload["id"],
                    email=payload["email"],
                    first_name=payload.get("firstName"),
                    status=payload.get("status"),
                    user_permissions=permissions if isinstance(permissions, dict) else {},
                ),
                cookies=dict(client.cookies.items()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Некорректный профиль ТехПортала", extra={"event": "auth.provider.invalid_profile", "fields": {}})
            raise ServiceUnavailableError("Сервис авторизации вернул некорректный профиль") from exc

    async def _get_csrf_token(self, client: httpx.AsyncClient, headers: dict[str, str]) -> str:
        response = await client.get(f"{self._settings.tp_origin_url}/csrf-token", headers=headers)
        response.raise_for_status()
        token = response.json().get("_csrf")
        if not isinstance(token, str) or not token:
            raise ServiceUnavailableError("Сервис авторизации не вернул CSRF-токен")
        return token

    async def _login(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        csrf_token: str,
        email: str,
        password: str,
    ) -> httpx.Response:
        return await client.post(
            f"{self._settings.tp_origin_url}/api/techportal-user/login",
            headers={**headers, "Content-Type": "application/json", "X-CSRF-Token": csrf_token},
            json={"email": email, "password": password},
        )
