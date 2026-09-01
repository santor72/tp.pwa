from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import fakeredis.aioredis
import pytest

from app.cache_store import CacheStore
from app.config import Settings
from app.errors import RepairCommentRequiredError, TicketNotFoundError
from app.actors import Actor
from app.schemas import TechPortalTicket, TechPortalUser
from app.tickets import TicketService

MOSCOW = ZoneInfo("Europe/Moscow")


class FakeTechPortal:
    def __init__(self, tickets: list[TechPortalTicket], *, sparse_persist: bool = False) -> None:
        self.ticket_result = tickets
        self.sparse_persist = sparse_persist
        self.ticket_calls: list[tuple[int | str, str]] = []
        self.persist_calls: list[tuple[int, dict[str, Any]]] = []
        self.comment_persist_calls: list[dict[str, Any]] = []
        self.user_calls = 0

    async def tickets(self, user_id: int | str | None, date: str) -> list[TechPortalTicket]:
        self.ticket_calls.append((user_id, date))
        return self.ticket_result

    async def users(self) -> list[TechPortalUser]:
        self.user_calls += 1
        return [TechPortalUser(id=3, name="Константин")]

    async def persist_ticket(self, ticket_id: int, tags: dict[str, Any]) -> TechPortalTicket:
        self.persist_calls.append((ticket_id, tags))
        if self.sparse_persist:
            return TechPortalTicket.model_validate({"id": ticket_id, "tags": tags})
        source = next(item for item in self.ticket_result if item.id == ticket_id)
        return source.model_copy(update={"tags": tags})

    async def ticket_by_id(self, ticket_id: int) -> dict[str, Any] | None:
        source = next((item for item in self.ticket_result if item.id == ticket_id), None)
        return source.model_dump(mode="json") if source else None

    async def persist_ticket_with_comment(self, ticket: dict[str, Any]) -> TechPortalTicket:
        self.comment_persist_calls.append(ticket)
        return TechPortalTicket.model_validate(ticket)


def ticket(
    ticket_id: int = 32412,
    *,
    masters: list[int] | None = None,
    tags: dict[str, Any] | None = None,
    scheduled_date: str | None = "2026-07-29T21:30:00.000Z",
) -> TechPortalTicket:
    return TechPortalTicket.model_validate({
        "id": ticket_id,
        "masters": masters if masters is not None else [87],
        "scheduledDate": scheduled_date,
        "description": "Описание работ",
        "tags": tags if tags is not None else {"Новое подключение": {}, "Солнечногорск": {}},
        "clientName": "Денис Денис",
        "clientPhone": "79254553958",
        "address": {
            "externalAddress": "СНТ Волга, участок 96",
            "house": "12",
            "apartment": "34",
        },
        "history": [{
            "createdAt": "2026-07-30T08:13:21.930Z",
            "techportalUser": 3,
            "changes": [{"key": "comments", "value": "Работы согласованы"}],
        }],
    })


def service(client: FakeTechPortal) -> TicketService:
    cache = CacheStore(fakeredis.aioredis.FakeRedis(decode_responses=True))
    return TicketService(Settings(tp_users_cache_ttl_seconds=300), client, cache)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_filters_foreign_tickets_and_normalizes_comments_and_utc() -> None:
    client = FakeTechPortal([ticket(), ticket(99, masters=[112])])

    result = await service(client).list_for_day(87, "today")

    assert len(result) == 1
    assert result[0].kind == "connection"
    assert result[0].completed is False
    assert result[0].comments[0].author == "Константин"
    assert result[0].comments[0].text == "Работы согласованы"
    assert result[0].assigned_masters == ["Пользователь #87"]
    assert result[0].comments[0].created_at is not None
    assert result[0].comments[0].created_at.utcoffset() == timedelta(hours=3)
    assert result[0].scheduled_at is not None
    assert result[0].scheduled_at.day == 30
    assert client.ticket_calls == [(87, datetime.now(MOSCOW).strftime("%d.%m.%Y"))]


@pytest.mark.asyncio
async def test_tomorrow_uses_moscow_calendar_date_and_user_names_are_cached() -> None:
    client = FakeTechPortal([ticket()])
    ticket_service = service(client)

    await ticket_service.list_for_day(87, "tomorrow")
    await ticket_service.list_for_day(87, "tomorrow")

    expected = (datetime.now(MOSCOW).date() + timedelta(days=1)).strftime("%d.%m.%Y")
    assert client.ticket_calls == [(87, expected), (87, expected)]
    assert client.user_calls == 1


@pytest.mark.asyncio
async def test_list_is_sorted_by_scheduled_time_with_missing_time_last() -> None:
    client = FakeTechPortal([
        ticket(3, scheduled_date=None),
        ticket(2, scheduled_date="2026-07-30T09:00:00.000Z"),
        ticket(1, scheduled_date="2026-07-30T06:00:00.000Z"),
    ])

    result = await service(client).list_for_day(87, "today")

    assert [item.id for item in result] == [1, 2, 3]


@pytest.mark.asyncio
async def test_all_scope_does_not_filter_and_marks_tickets_read_only() -> None:
    client = FakeTechPortal([ticket(), ticket(99, masters=[112])])
    actor = Actor(user_id=uuid4(), techportal_user_id="87", channel="pwa")

    result = await service(client).list_for_actor(actor, "today", "all")

    assert [item.id for item in result] == [32412, 99]
    assert all(not item.can_change_completion for item in result)
    assert result[1].assigned_masters == ["Пользователь #112"]
    assert client.ticket_calls[0][0] is None


@pytest.mark.asyncio
async def test_address_skips_empty_house_and_apartment() -> None:
    source = ticket()
    source.address = source.address.model_copy(update={"house": "  ", "apartment": None})

    result = await service(FakeTechPortal([source])).list_for_day(87, "today")

    assert result[0].address == "СНТ Волга, участок 96"


@pytest.mark.asyncio
async def test_completion_uses_authoritative_full_tags_and_can_remove_marker() -> None:
    source = ticket(tags={"Новое подключение": {}, "Солнечногорск": {}})
    client = FakeTechPortal([source])
    ticket_service = service(client)

    completed = await ticket_service.set_completed(87, "today", source.id, True)
    assert client.persist_calls[-1] == (
        source.id,
        {"Новое подключение": {}, "Солнечногорск": {}, "Работы произведены": {}},
    )
    assert completed.completed is True
    assert completed.address == "СНТ Волга, участок 96, дом 12, кв. 34"
    assert completed.client_phone == "+79254553958"
    assert completed.client_phones == ["+79254553958"]
    assert completed.client_name == "Денис Денис"
    assert completed.description == "Описание работ"
    assert completed.comments[0].text == "Работы согласованы"

    client.ticket_result = [source.model_copy(update={"tags": completed.tags})]
    reopened = await ticket_service.set_completed(87, "today", source.id, False)
    assert client.persist_calls[-1] == (
        source.id,
        {"Новое подключение": {}, "Солнечногорск": {}},
    )
    assert reopened.completed is False


@pytest.mark.asyncio
async def test_completion_rejects_ticket_not_assigned_to_session_user() -> None:
    client = FakeTechPortal([ticket(masters=[112])])

    with pytest.raises(TicketNotFoundError):
        await service(client).set_completed(87, "today", 32412, True)

    assert client.persist_calls == []


@pytest.mark.asyncio
async def test_repair_requires_comment_and_persists_it_with_completion() -> None:
    source = ticket(tags={"Заявка на выезд": {}})
    client = FakeTechPortal([source])
    ticket_service = service(client)

    with pytest.raises(RepairCommentRequiredError):
        await ticket_service.set_completed(87, "today", source.id, True)
    assert client.comment_persist_calls == []

    result = await ticket_service.set_completed(87, "today", source.id, True, "Заменили кабель")

    assert result.completed is True
    assert client.comment_persist_calls == [{
        **source.model_dump(mode="json"),
        "tags": {"Заявка на выезд": {}, "Работы произведены": {}},
        "comments": "Заменили кабель",
    }]


@pytest.mark.asyncio
async def test_completion_preserves_card_for_sparse_persist_response() -> None:
    source = ticket()
    client = FakeTechPortal([source], sparse_persist=True)

    result = await service(client).set_completed(87, "today", source.id, True)

    assert result.completed is True
    assert result.address == "СНТ Волга, участок 96, дом 12, кв. 34"
    assert result.client_name == "Денис Денис"
    assert result.comments[0].author == "Константин"


def test_phone_normalization_deduplicates_equivalent_numbers_and_keeps_unparseable_values() -> None:
    source = ticket()
    source.phones = ["+7 925 455-39-58", "неизвестный", "неизвестный"]

    assert TicketService._phones(source) == ["+79254553958", "неизвестный"]
