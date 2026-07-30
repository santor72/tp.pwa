from typing import Any

import fakeredis.aioredis
import pytest

from app.cache_store import CacheStore
from app.config import Settings
from app.domofon import DomofonService
from app.errors import FlatAlreadyAssignedError
from app.schemas import DomofonConnectRequest, DomofonCreateRequest


class FakeEsb:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, params, json_body))
        return self.responses.pop(0)


def service(esb: FakeEsb) -> DomofonService:
    cache = CacheStore(fakeredis.aioredis.FakeRedis(decode_responses=True))
    return DomofonService(Settings(), esb, cache)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_addresses_are_normalized_by_contract() -> None:
    esb = FakeEsb([{
        "ok": True,
        "locations": [
            {
                "locid": 4217,
                "title": "Резервное название",
                "ufCrm11_1771419206": "Московская область, Земская, 5|55.1;37.4",
            },
            {"locid": 4297, "title": "Уездная, 4", "ufCrm11_1771419206": ""},
        ],
    }])

    domofon = service(esb)
    addresses = await domofon.addresses()
    cached_addresses = await domofon.addresses()

    assert [item.model_dump() for item in addresses] == [
        {"locid": 4217, "loctext": "Московская область, Земская, 5"},
        {"locid": 4297, "loctext": "Уездная, 4"},
    ]
    assert cached_addresses == addresses
    assert esb.calls == [("GET", "locations", None, None)]


@pytest.mark.asyncio
async def test_connect_maps_internal_login_and_normalizes_result() -> None:
    esb = FakeEsb([{"ok": True, "reason": "Услуга подключена"}])

    result = await service(esb).connect(DomofonConnectRequest(service_login="client-10"))

    assert result.model_dump() == {"ok": True, "reason": "Услуга подключена"}
    assert esb.calls == [
        ("POST", "add-domofon-to-user", None, {"login": "client-10"}),
    ]


@pytest.mark.asyncio
async def test_create_checks_flat_then_creates_user() -> None:
    esb = FakeEsb([
        {"ok": True, "flats": [{"flat": "143", "login": ""}]},
        {"ok": True, "reason": "Создан пользователь 15600453"},
    ])
    payload = DomofonCreateRequest(
        locid=4217,
        field_flat=143,
        field_podezd=2,
        client_name="Иванов Иван",
    )

    result = await service(esb).create(payload)

    assert result.reason == "Создан пользователь 15600453"
    assert esb.calls == [
        ("GET", "flat-search", {"locid": 4217, "field_flat": 143}, None),
        ("POST", "domofon-new-user", None, payload.model_dump(exclude_none=True)),
    ]


@pytest.mark.asyncio
async def test_create_stops_when_flat_has_login() -> None:
    esb = FakeEsb([{"ok": True, "flats": [{"flat": "143", "login": "existing-user"}]}])
    payload = DomofonCreateRequest(
        locid=4217,
        field_flat=143,
        field_podezd=2,
        client_name="Иванов Иван",
    )

    with pytest.raises(FlatAlreadyAssignedError):
        await service(esb).create(payload)

    assert len(esb.calls) == 1
