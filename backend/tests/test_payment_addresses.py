import pytest

from app.config import Settings
from app.payment_addresses import PaymentAddressService


class Cache:
    def __init__(self): self.saved = None
    async def get_json(self, key): return None
    async def set_json(self, key, value, ttl): self.saved = (key, value, ttl)


class Esb:
    async def request(self, method, path):
        assert (method, path) == ("GET", "locations")
        return {"locations": [
            {"locid": "10", "ufCrm11_1771419206": "Москва, Тверская, 1 | служебная часть"},
            {"locid": 11, "title": "Москва, Арбат, 2"},
        ]}


@pytest.mark.asyncio
async def test_payment_addresses_keep_full_display_text_and_locid() -> None:
    cache = Cache()
    result = await PaymentAddressService(Settings(payment_addresses_cache_ttl_seconds=42), Esb(), cache).list()
    assert [item.model_dump() for item in result] == [
        {"locid": 10, "loctext": "Москва, Тверская, 1"},
        {"locid": 11, "loctext": "Москва, Арбат, 2"},
    ]
    assert cache.saved == ("payments:addresses:v1", [item.model_dump() for item in result], 42)
