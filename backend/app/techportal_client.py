import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.errors import (
    ServiceUnavailableError,
    TechPortalAuthError,
    TechPortalCallError,
    TechPortalNotConfiguredError,
    TechPortalResponseError,
)
from app.schemas import TechPortalTicket, TechPortalUser

logger = logging.getLogger(__name__)


class TechPortalClient:
    """Серверный клиент API заявок ТехПортала."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def tickets(self, user_id: int | str | None, date: str) -> list[TechPortalTicket]:
        ticket_filter: dict[str, Any] = {
            "tags": {},
            "createdBy": [],
            "closedFrom": "-",
            "scheduledTo": date,
            "scheduledFrom": date,
        }
        if user_id is not None:
            ticket_filter["masterIds"] = [user_id]
        payload = {
            "page": 0,
            "filters": {
                "and": [
                    ticket_filter
                ]
            },
        }
        data = await self._request("POST", "tickets/get", json=payload)
        if not isinstance(data, list):
            raise TechPortalResponseError()
        try:
            return [TechPortalTicket.model_validate(item) for item in data]
        except ValidationError as exc:
            raise TechPortalResponseError() from exc

    async def users(self) -> list[TechPortalUser]:
        data = await self._request("GET", "techportal-user/list")
        if not isinstance(data, list):
            raise TechPortalResponseError()
        try:
            return [TechPortalUser.model_validate(item) for item in data]
        except ValidationError as exc:
            raise TechPortalResponseError() from exc

    async def persist_ticket(self, ticket_id: int, tags: dict[str, Any]) -> TechPortalTicket:
        data = await self._request(
            "POST",
            "tickets/persist",
            json={"ticket": {"id": ticket_id, "tags": tags}},
        )
        if not isinstance(data, dict):
            raise TechPortalResponseError()
        try:
            return TechPortalTicket.model_validate(data)
        except ValidationError as exc:
            raise TechPortalResponseError() from exc

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        if not self._settings.tp_base_url or not self._settings.tp_base_token:
            raise TechPortalNotConfiguredError()
        url = f"{self._settings.tp_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._settings.tp_base_token}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.tp_api_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning(
                "ТехПортал недоступен",
                extra={"event": "techportal.request.unavailable", "fields": {"error": type(exc).__name__}},
            )
            raise ServiceUnavailableError("ТехПортал временно недоступен") from exc

        if response.status_code in {401, 403}:
            logger.error(
                "ТехПортал отклонил системный токен",
                extra={"event": "techportal.auth.failed", "fields": {"upstream_status": response.status_code}},
            )
            raise TechPortalAuthError()
        if response.is_error:
            logger.warning(
                "Ошибка API ТехПортала",
                extra={"event": "techportal.request.failed", "fields": {"upstream_status": response.status_code}},
            )
            raise TechPortalCallError()
        try:
            return response.json()
        except ValueError as exc:
            raise TechPortalResponseError() from exc
