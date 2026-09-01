from app.cache_store import CacheStore
from app.config import Settings
from app.errors import EsbResponseError
from app.esb_client import EsbClient
from app.schemas import PaymentAddress


class PaymentAddressService:
    cache_key = "payments:addresses:v1"

    def __init__(self, settings: Settings, esb: EsbClient, cache: CacheStore) -> None:
        self._settings = settings
        self._esb = esb
        self._cache = cache

    async def list(self) -> list[PaymentAddress]:
        cached = await self._cache.get_json(self.cache_key)
        if cached is not None:
            return [PaymentAddress.model_validate(item) for item in cached]
        result = await self._esb.request("GET", "locations")
        locations = result.get("locations")
        if not isinstance(locations, list):
            raise EsbResponseError("ESB не вернул список адресов")
        addresses: list[PaymentAddress] = []
        for item in locations:
            if not isinstance(item, dict):
                raise EsbResponseError("ESB вернул некорректный адрес")
            try:
                locid = int(item["locid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EsbResponseError("ESB вернул некорректный адрес") from exc
            external_text = item.get("ufCrm11_1771419206")
            loctext = external_text.split("|", 1)[0].strip() if isinstance(external_text, str) else ""
            if not loctext and isinstance(item.get("title"), str):
                loctext = item["title"].strip()
            if not loctext:
                raise EsbResponseError("ESB вернул адрес без названия")
            addresses.append(PaymentAddress(locid=locid, loctext=loctext))
        await self._cache.set_json(self.cache_key, [address.model_dump() for address in addresses], self._settings.payment_addresses_cache_ttl_seconds)
        return addresses
