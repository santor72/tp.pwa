import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.actors import Actor
from app.cache_store import CacheStore
from app.config import Settings
from app.errors import TicketNotFoundError
from app.schemas import (
    TechPortalTicket,
    TechPortalUser,
    TicketComment,
    TicketResponse,
)
from app.techportal_client import TechPortalClient

MOSCOW = ZoneInfo("Europe/Moscow")
COMPLETED_TAG = "Работы произведены"
CONNECTION_TAG = "Новое подключение"


class TicketService:
    users_cache_key = "techportal:users:v1"

    def __init__(self, settings: Settings, client: TechPortalClient, cache: CacheStore) -> None:
        self._settings = settings
        self._client = client
        self._cache = cache

    async def list_for_day(
        self,
        user_id: int | str,
        day: Literal["today", "tomorrow"],
    ) -> list[TicketResponse]:
        offset = 0 if day == "today" else 1
        target = datetime.now(MOSCOW).date() + timedelta(days=offset)
        tickets = await self._client.tickets(user_id, target.strftime("%d.%m.%Y"))
        # Даже при ошибке upstream-фильтра не раскрываем чужие заявки.
        tickets = [ticket for ticket in tickets if self._is_master(ticket, user_id)]
        users = await self._user_names()
        normalized = [self._normalize(ticket, users) for ticket in tickets]
        normalized.sort(
            key=lambda ticket: (
                ticket.scheduled_at is None,
                ticket.scheduled_at or datetime.max.replace(tzinfo=UTC),
            )
        )
        return normalized

    async def list_for_actor(
        self,
        actor: Actor,
        day: Literal["today", "tomorrow"],
        scope: Literal["assigned", "all"] = "assigned",
    ) -> list[TicketResponse]:
        if scope == "assigned":
            return await self.list_for_day(actor.techportal_user_id, day)
        offset = 0 if day == "today" else 1
        target = datetime.now(MOSCOW).date() + timedelta(days=offset)
        tickets = await self._client.tickets(None, target.strftime("%d.%m.%Y"))
        users = await self._user_names()
        normalized = [self._normalize(ticket, users).model_copy(update={"can_change_completion": False}) for ticket in tickets]
        normalized.sort(
            key=lambda ticket: (
                ticket.scheduled_at is None,
                ticket.scheduled_at or datetime.max.replace(tzinfo=UTC),
            )
        )
        return normalized

    async def set_completed(
        self,
        user_id: int | str,
        day: Literal["today", "tomorrow"],
        ticket_id: int,
        completed: bool,
    ) -> TicketResponse:
        offset = 0 if day == "today" else 1
        target = datetime.now(MOSCOW).date() + timedelta(days=offset)
        tickets = await self._client.tickets(user_id, target.strftime("%d.%m.%Y"))
        ticket = next(
            (
                item
                for item in tickets
                if item.id == ticket_id and self._is_master(item, user_id)
            ),
            None,
        )
        if ticket is None:
            raise TicketNotFoundError()
        updated_tags = dict(ticket.tags)
        if completed:
            updated_tags[COMPLETED_TAG] = {}
        else:
            updated_tags.pop(COMPLETED_TAG, None)
        persisted = await self._client.persist_ticket(ticket_id, updated_tags)
        # tickets/persist может вернуть только изменённые поля (например id и
        # tags). Сохраняем полные данные уже загруженной заявки для карточки.
        persisted_fields = {
            field_name: getattr(persisted, field_name)
            for field_name in persisted.model_fields_set
        }
        ticket = ticket.model_copy(update=persisted_fields)
        users = await self._user_names()
        return self._normalize(ticket, users)

    async def set_completed_for_actor(
        self,
        actor: Actor,
        day: Literal["today", "tomorrow"],
        ticket_id: int,
        completed: bool,
    ) -> TicketResponse:
        return await self.set_completed(actor.techportal_user_id, day, ticket_id, completed)

    async def _user_names(self) -> dict[str, str]:
        cached = await self._cache.get_json(self.users_cache_key)
        if isinstance(cached, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in cached.items()):
            return cached
        users = await self._client.users()
        names = {str(user.id): self._user_name(user) for user in users}
        await self._cache.set_json(
            self.users_cache_key,
            names,
            self._settings.tp_users_cache_ttl_seconds,
        )
        return names

    def _normalize(self, ticket: TechPortalTicket, users: dict[str, str]) -> TicketResponse:
        phone = ticket.clientPhone or (ticket.phones[0] if ticket.phones else "")
        comments: list[TicketComment] = []
        for history_item in ticket.history:
            for change in history_item.changes:
                if change.key != "comments":
                    continue
                comments.append(
                    TicketComment(
                        created_at=self._to_moscow(history_item.createdAt),
                        author=users.get(
                            str(history_item.techportalUser),
                            f"Пользователь #{history_item.techportalUser}",
                        ),
                        text=self._comment_text(change.value),
                    )
                )
        comments.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
        return TicketResponse(
            id=ticket.id,
            address=self._address_text(ticket),
            client_phone=phone,
            client_name=ticket.clientName or "",
            description=ticket.description or "",
            scheduled_at=self._to_moscow(ticket.scheduledDate),
            kind="connection" if CONNECTION_TAG in ticket.tags else "repair",
            completed=COMPLETED_TAG in ticket.tags,
            tags=ticket.tags,
            comments=comments,
            assigned_masters=[users.get(str(master_id), f"Пользователь #{master_id}") for master_id in ticket.masters],
        )

    @staticmethod
    def _is_master(ticket: TechPortalTicket, user_id: int | str) -> bool:
        return any(str(master_id) == str(user_id) for master_id in ticket.masters)

    @staticmethod
    def _address_text(ticket: TechPortalTicket) -> str:
        if ticket.address is None:
            return ""
        parts: list[str] = []
        if ticket.address.externalAddress and ticket.address.externalAddress.strip():
            parts.append(ticket.address.externalAddress.strip())
        if ticket.address.house is not None and str(ticket.address.house).strip():
            parts.append(f"дом {str(ticket.address.house).strip()}")
        if ticket.address.apartment is not None and str(ticket.address.apartment).strip():
            parts.append(f"кв. {str(ticket.address.apartment).strip()}")
        return ", ".join(parts)

    @staticmethod
    def _to_moscow(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(MOSCOW)

    @staticmethod
    def _user_name(user: TechPortalUser) -> str:
        if user.name and user.name.strip():
            return user.name.strip()
        composed = " ".join(part.strip() for part in (user.lastName, user.firstName) if part and part.strip())
        return composed or f"Пользователь #{user.id}"

    @staticmethod
    def _comment_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
