import logging
from typing import Any

import httpx

from app.config import Settings
from app.errors import (
    ApiError,
    EsbCallError,
    EsbNotConfiguredError,
    EsbResponseError,
    ServiceUnavailableError,
)
from app.logging import audit

logger = logging.getLogger(__name__)


class EsbClient:
    """HTTP-клиент ESB. Токен существует только на backend."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._settings.esb_base_url or not self._settings.esb_base_token:
            raise EsbNotConfiguredError()

        url = f"{self._settings.esb_base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.esb_timeout_seconds),
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {self._settings.esb_base_token}",
                        "Accept": "application/json",
                    },
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "ESB недоступен",
                extra={
                    "event": "esb.unavailable",
                    "fields": {"error": type(exc).__name__, "operation": path},
                },
            )
            raise ServiceUnavailableError("ESB временно недоступен") from exc

        if response.status_code == 401:
            audit(
                logger,
                "esb.auth.failed",
                result="failure",
                upstream_status=401,
                operation=path,
            )
            raise ApiError(
                502,
                "ESB_AUTH_FAILED",
                "Ошибка системной авторизации ESB",
            )
        if response.is_server_error:
            raise ServiceUnavailableError("ESB временно недоступен")
        if response.is_error:
            raise ApiError(502, "ESB_HTTP_ERROR", "ESB вернул ошибку HTTP")

        try:
            payload = response.json()
        except ValueError as exc:
            raise EsbResponseError() from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise EsbResponseError()
        if payload["ok"] is False:
            reason = payload.get("reason")
            message = reason.strip() if isinstance(reason, str) and reason.strip() else "ESB не выполнил операцию"
            raise EsbCallError(message)
        return payload
