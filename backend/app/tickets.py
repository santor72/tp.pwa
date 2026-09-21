import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import phonenumbers

from app.actors import Actor
from app.cache_store import CacheStore
from app.config import Settings
from app.errors import ApiError, RepairCommentRequiredError, TicketNotFoundError
from app.schemas import (
    TechPortalBrigade,
    TechPortalTicket,
    TechPortalUser,
    TicketFilterBrigade,
    TicketFilterMaster,
    TicketFiltersResponse,
    TicketComment,
    TicketResponse,
)
from app.techportal_client import TechPortalClient

MOSCOW = ZoneInfo("Europe/Moscow")
COMPLETED_TAG = "Работы произведены"
CONNECTION_TAG = "Новое подключение"


class TicketService:
    users_cache_key = "techportal:users:v1"
    users_details_cache_key = "techportal:users:details:v1"
    brigades_cache_key = "techportal:brigades:v1"

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
        brigade_ids: list[str] | None = None,
        master_ids: list[str] | None = None,
    ) -> list[TicketResponse]:
        if scope == "assigned":
            return await self.list_for_day(actor.techportal_user_id, day)
        resolved_master_ids = await self._resolve_filter_master_ids(brigade_ids, master_ids)
        if resolved_master_ids is not None and not resolved_master_ids:
            return []
        offset = 0 if day == "today" else 1
        target = datetime.now(MOSCOW).date() + timedelta(days=offset)
        if resolved_master_ids is None:
            tickets = await self._client.tickets(None, target.strftime("%d.%m.%Y"))
        else:
            tickets = await self._client.tickets(None, target.strftime("%d.%m.%Y"), resolved_master_ids)
        users = await self._user_names()
        normalized = [self._normalize(ticket, users).model_copy(update={"can_change_completion": False}) for ticket in tickets]
        normalized.sort(
            key=lambda ticket: (
                ticket.scheduled_at is None,
                ticket.scheduled_at or datetime.max.replace(tzinfo=UTC),
            )
        )
        return normalized

    async def filters(self) -> TicketFiltersResponse:
        users = await self._users()
        brigades = await self._brigades()
        users_by_id = {str(user.id): user for user in users}
        brigade_master_ids = {
            str(master_id)
            for brigade in brigades
            for master_id in brigade.master_ids
        }
        masters = [
            TicketFilterMaster(id=str(user.id), name=self._user_name(user))
            for user in users
            if str(user.id) in brigade_master_ids
        ]
        masters.sort(key=lambda item: item.name.casefold())
        result_brigades = []
        for brigade in brigades:
            master_ids = [str(master_id) for master_id in brigade.master_ids]
            surnames = [
                self._user_surname(users_by_id[master_id])
                for master_id in master_ids
                if master_id in users_by_id
            ]
            if not surnames:
                continue
            result_brigades.append(TicketFilterBrigade(
                id=str(brigade.id),
                name=", ".join(dict.fromkeys(surnames)),
                master_ids=master_ids,
            ))
        result_brigades.sort(key=lambda item: item.name.casefold())
        return TicketFiltersResponse(masters=masters, brigades=result_brigades)

    async def set_completed(
        self,
        user_id: int | str,
        day: Literal["today", "tomorrow"],
        ticket_id: int,
        completed: bool,
        comment: str | None = None,
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
        is_repair = CONNECTION_TAG not in ticket.tags
        if completed and not is_repair:
            raise ApiError(422, 'CONNECTION_REPORT_REQUIRED', 'Для выполнения подключения заполните отчёт')
        if completed and is_repair:
            if not comment:
                raise RepairCommentRequiredError()
            raw_ticket = await self._client.ticket_by_id(ticket_id)
            if raw_ticket is None:
                raise TicketNotFoundError()
            ticket = TechPortalTicket.model_validate(raw_ticket)
            if not self._is_master(ticket, user_id):
                raise TicketNotFoundError()
        else:
            raw_ticket = None
        updated_tags = dict(ticket.tags)
        if completed:
            updated_tags[COMPLETED_TAG] = {}
        else:
            updated_tags.pop(COMPLETED_TAG, None)
        if raw_ticket is not None:
            raw_ticket["tags"] = updated_tags
            raw_ticket["comments"] = comment
            persisted = await self._client.persist_ticket_with_comment(raw_ticket)
        else:
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
        comment: str | None = None,
    ) -> TicketResponse:
        return await self.set_completed(actor.techportal_user_id, day, ticket_id, completed, comment)

    async def assert_connection_assigned(self, user_id: int | str, day: str, ticket_id: int) -> None:
        if day not in {'today', 'tomorrow'}:
            raise TicketNotFoundError()
        offset = 0 if day == 'today' else 1
        target = datetime.now(MOSCOW).date() + timedelta(days=offset)
        tickets = await self._client.tickets(user_id, target.strftime('%d.%m.%Y'))
        ticket = next((item for item in tickets if item.id == ticket_id and self._is_master(item, user_id)), None)
        if ticket is None or CONNECTION_TAG not in ticket.tags:
            raise TicketNotFoundError()

    async def mark_connection_completed(self, user_id: int | str, day: str, ticket_id: int, comment: str) -> TicketResponse:
        if not comment.strip():
            raise ApiError(422, 'CONNECTION_REPORT_REQUIRED', 'Добавьте текст отчёта или фотографию')
        await self.assert_connection_assigned(user_id, day, ticket_id)
        raw_ticket = await self._client.ticket_by_id(ticket_id)
        if raw_ticket is None:
            raise TicketNotFoundError()
        ticket = TechPortalTicket.model_validate(raw_ticket)
        if not self._is_master(ticket, user_id) or CONNECTION_TAG not in ticket.tags:
            raise TicketNotFoundError()
        updated_tags = dict(ticket.tags)
        updated_tags[COMPLETED_TAG] = {}
        raw_ticket['tags'] = updated_tags
        raw_ticket['comments'] = comment
        persisted = await self._client.persist_ticket_with_comment(raw_ticket)
        persisted_fields = {field_name: getattr(persisted, field_name) for field_name in persisted.model_fields_set}
        users = await self._user_names()
        return self._normalize(ticket.model_copy(update=persisted_fields), users)

    async def connection_completion_recorded(self, user_id: int | str, ticket_id: int, comment: str) -> bool:
        raw_ticket = await self._client.ticket_by_id(ticket_id)
        if raw_ticket is None:
            return False
        ticket = TechPortalTicket.model_validate(raw_ticket)
        if not self._is_master(ticket, user_id) or CONNECTION_TAG not in ticket.tags or COMPLETED_TAG not in ticket.tags:
            return False
        return any(
            any(change.key == 'comments' and self._comment_text(change.value) == comment for change in entry.changes)
            for entry in ticket.history
        )

    async def dial(self, phone: str, upstream_cookies: dict[str, str]) -> None:
        await self._client.dial(phone, upstream_cookies)

    async def _user_names(self) -> dict[str, str]:
        cached = await self._cache.get_json(self.users_cache_key)
        if isinstance(cached, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in cached.items()):
            return cached
        users = await self._users()
        names = {str(user.id): self._user_name(user) for user in users}
        await self._cache.set_json(
            self.users_cache_key,
            names,
            self._settings.tp_users_cache_ttl_seconds,
        )
        return names

    async def _users(self) -> list[TechPortalUser]:
        cached = await self._cache.get_json(self.users_details_cache_key)
        if isinstance(cached, list):
            try:
                return [TechPortalUser.model_validate(item) for item in cached]
            except ValueError:
                pass
        users = await self._client.users()
        await self._cache.set_json(
            self.users_details_cache_key,
            [user.model_dump(mode="json") for user in users],
            self._settings.tp_users_cache_ttl_seconds,
        )
        return users

    async def _brigades(self) -> list[TechPortalBrigade]:
        cached = await self._cache.get_json(self.brigades_cache_key)
        if isinstance(cached, list):
            try:
                return [TechPortalBrigade.model_validate(item) for item in cached]
            except ValueError:
                pass
        brigades = await self._client.brigades()
        await self._cache.set_json(
            self.brigades_cache_key,
            [brigade.model_dump(mode="json") for brigade in brigades],
            self._settings.tp_users_cache_ttl_seconds,
        )
        return brigades

    async def _resolve_filter_master_ids(
        self,
        brigade_ids: list[str] | None,
        master_ids: list[str] | None,
    ) -> list[str] | None:
        if not brigade_ids and not master_ids:
            return None
        resolved = set(master_ids or [])
        if brigade_ids:
            brigades = await self._brigades()
            requested = set(brigade_ids)
            resolved.update(
                str(master_id)
                for brigade in brigades
                if str(brigade.id) in requested
                for master_id in brigade.master_ids
            )
        return sorted(resolved)

    def _normalize(self, ticket: TechPortalTicket, users: dict[str, str]) -> TicketResponse:
        phones = self._phones(ticket)
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
            client_phone=phones[0] if phones else "",
            client_phones=phones,
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
    def _phones(ticket: TechPortalTicket) -> list[str]:
        phones: list[str] = []
        for value in (ticket.clientPhone, *ticket.phones):
            if not value or not value.strip():
                continue
            phone = value.strip()
            try:
                if not any(character.isdigit() for character in phone):
                    raise phonenumbers.NumberParseException(phonenumbers.NumberParseException.NOT_A_NUMBER, "no digits")
                phone = phonenumbers.format_number(
                    phonenumbers.parse(phone, "RU"),
                    phonenumbers.PhoneNumberFormat.E164,
                )
            except phonenumbers.NumberParseException:
                pass
            if phone not in phones:
                phones.append(phone)
        return phones

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
    def _user_surname(user: TechPortalUser) -> str:
        if user.lastName and user.lastName.strip():
            return user.lastName.strip()
        if user.name and user.name.strip():
            return user.name.strip().split()[0]
        return f"Пользователь #{user.id}"

    @staticmethod
    def _comment_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
