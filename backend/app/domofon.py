from typing import Any

from app.cache_store import CacheStore

from app.config import Settings
from app.errors import EsbResponseError, FlatAlreadyAssignedError
from app.esb_client import EsbClient
from app.schemas import (
    DomofonAddress,
    DomofonConnectRequest,
    DomofonCreateRequest,
    DomofonOperationResponse,
)


class DomofonService:
    """Сценарии Домофона и адаптер внешнего контракта ESB."""

    addresses_cache_key = "domofon:addresses:v1"

    def __init__(self, settings: Settings, esb: EsbClient, cache: CacheStore) -> None:
        self._settings = settings
        self._esb = esb
        self._cache = cache

    async def connect(self, payload: DomofonConnectRequest) -> DomofonOperationResponse:
        result = await self._esb.request(
            "POST",
            "add-domofon-to-user",
            json_body={"login": payload.service_login},
        )
        return self._operation_result(result)

    async def addresses(self) -> list[DomofonAddress]:
        cached = await self._cache.get_json(self.addresses_cache_key)
        if cached is not None:
            return [DomofonAddress.model_validate(item) for item in cached]

        result = await self._esb.request("GET", "locations")
        locations = result.get("locations")
        if not isinstance(locations, list):
            raise EsbResponseError("ESB не вернул список адресов")

        addresses: list[DomofonAddress] = []
        for item in locations:
            if not isinstance(item, dict):
                raise EsbResponseError("ESB вернул некорректный адрес")
            try:
                locid = int(item["locid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EsbResponseError("ESB вернул некорректный адрес") from exc
            external_text = item.get("ufCrm11_1771419206")
            loctext = ""
            if isinstance(external_text, str):
                loctext = external_text.split("|", 1)[0].strip()
            if not loctext and isinstance(item.get("title"), str):
                loctext = item["title"].strip()
            if not loctext:
                raise EsbResponseError("ESB вернул адрес без названия")
            addresses.append(DomofonAddress(locid=locid, loctext=loctext))

        await self._cache.set_json(
            self.addresses_cache_key,
            [address.model_dump() for address in addresses],
            self._settings.domofon_addresses_cache_ttl_seconds,
        )
        return addresses

    async def create(self, payload: DomofonCreateRequest) -> DomofonOperationResponse:
        search = await self._esb.request(
            "GET",
            "flat-search",
            params={"locid": payload.locid, "field_flat": payload.field_flat},
        )
        flats = search.get("flats")
        if not isinstance(flats, list):
            raise EsbResponseError("ESB не вернул результат поиска квартиры")
        for flat in flats:
            if not isinstance(flat, dict):
                raise EsbResponseError("ESB вернул некорректную квартиру")
            login = flat.get("login")
            if login is not None and str(login).strip():
                raise FlatAlreadyAssignedError()

        result = await self._esb.request(
            "POST",
            "domofon-new-user",
            json_body=payload.model_dump(exclude_none=True),
        )
        return self._operation_result(result)

    @staticmethod
    def _operation_result(result: dict[str, Any]) -> DomofonOperationResponse:
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise EsbResponseError("ESB не вернул описание результата")
        return DomofonOperationResponse(ok=True, reason=reason.strip())
