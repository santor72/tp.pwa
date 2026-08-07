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
            "scheduledTo": date,
            "scheduledFrom": date,
        }
        if user_id is not None:
            ticket_filter["masterIds"] = [user_id]
        tickets: dict[int, TechPortalTicket] = {}
        for page in range(self._settings.tp_tickets_max_pages):
            payload = {"page": page, "filters": {"and": [ticket_filter]}}
            data = await self._request("POST", "tickets/get", json=payload)
            if not isinstance(data, list):
                raise TechPortalResponseError()
            try:
                page_tickets = [TechPortalTicket.model_validate(item) for item in data]
            except ValidationError as exc:
                raise TechPortalResponseError() from exc
            if not page_tickets:
                return list(tickets.values())
            new_ticket_count = sum(ticket.id not in tickets for ticket in page_tickets)
            tickets.update({ticket.id: ticket for ticket in page_tickets})
            if new_ticket_count == 0:
                logger.warning("Повтор страницы заявок ТехПортала", extra={"event": "techportal.tickets.repeated_page", "fields": {"page": page}})
                return list(tickets.values())
        logger.warning(
            "Достигнут лимит страниц заявок ТехПортала",
            extra={"event": "techportal.tickets.page_limit", "fields": {"max_pages": self._settings.tp_tickets_max_pages}},
        )
        return list(tickets.values())

    async def users(self) -> list[TechPortalUser]:
        data = await self._request("GET", "techportal-user/list")
        if not isinstance(data, list):
            raise TechPortalResponseError()
        try:
            return [TechPortalUser.model_validate(item) for item in data]
        except ValidationError as exc:
            raise TechPortalResponseError() from exc

    async def ticket_by_id(self, ticket_id: int) -> dict[str, Any] | None:
        data = await self._request(
            "POST",
            "tickets/get",
            json={"page": 0, "filters": {"and": [{"createdBy": [], "masterIds": [], "id": ticket_id}]}},
        )
        if not isinstance(data, list):
            raise TechPortalResponseError()
        return next((item for item in data if isinstance(item, dict) and item.get("id") == ticket_id), None)

    async def persist_ticket_with_comment(self, ticket: dict[str, Any]) -> TechPortalTicket:
        data = await self._request("POST", "tickets/persist", json={"ticket": ticket})
        if not isinstance(data, dict):
            raise TechPortalResponseError()
        try:
            return TechPortalTicket.model_validate(data)
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

    async def dial(self, phone: str, cookies: dict[str, str]) -> None:
        if not cookies:
            raise TechPortalAuthError()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self._settings.tp_origin_url}/",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.tp_api_timeout_seconds,
                transport=self._transport,
                cookies=cookies,
            ) as client:
                csrf_response = await client.get(f"{self._settings.tp_origin_url}/csrf-token", headers=headers)
                if csrf_response.status_code in {401, 403}:
                    raise TechPortalAuthError()
                csrf_response.raise_for_status()
                csrf_token = csrf_response.json().get("_csrf")
                if not isinstance(csrf_token, str) or not csrf_token:
                    raise TechPortalResponseError()
                response = await client.post(
                    f"{self._settings.tp_origin_url}/api/conversations/dial",
                    headers={**headers, "Content-Type": "application/json", "X-CSRF-Token": csrf_token},
                    json={"phone": phone},
                )
        except TechPortalAuthError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("ТехПортал недоступен", extra={"event": "techportal.dial.unavailable", "fields": {"error": type(exc).__name__}})
            raise ServiceUnavailableError("ТехПортал временно недоступен") from exc
        if response.status_code in {401, 403}:
            raise TechPortalAuthError()
        if response.is_error:
            raise TechPortalCallError()

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        url = f"{self._settings.tp_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        return await self._request_url(method, url, **kwargs)

    async def _request_url(self, method: str, url: str, **kwargs: Any) -> Any:
        if not self._settings.tp_base_url or not self._settings.tp_base_token:
            raise TechPortalNotConfiguredError()
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
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise TechPortalResponseError() from exc
