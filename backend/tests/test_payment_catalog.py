from decimal import Decimal

import pytest

from app.config import Settings
from app.errors import Bitrix24Error, PaymentsNotConfiguredError
from app.payment_catalog import PaymentProductCatalog


class Cache:
    def __init__(self, value=None): self.value = value; self.saved = None
    async def get_json(self, key): return self.value
    async def set_json(self, key, value, ttl): self.saved = (key, value, ttl)


class Bitrix:
    def __init__(self, *, active="Y", price="1500.00", currency="RUB"):
        self.active = active; self.price = price; self.currency = currency; self.calls = []
    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "catalog.product.get":
            return {"product": {"id": 123, "active": self.active}}
        return {"prices": [{"price": self.price, "currency": self.currency, "catalogGroupId": 2}]}


@pytest.mark.asyncio
async def test_catalog_exposes_only_allowlisted_product_and_uses_bitrix_price() -> None:
    cache = Cache(); bitrix = Bitrix()
    settings = Settings(bx24_webhook="https://bx.test/rest/1/secret", bx24_payment_products={"Выезд": 123})
    products = await PaymentProductCatalog(settings, bitrix, cache).list()
    assert [(item.product_id, item.title, item.default_amount) for item in products] == [(123, "Выезд", Decimal("1500.00"))]
    assert bitrix.calls[1] == ("catalog.price.list", {"filter": {"productId": 123, "catalogGroupId": 2}, "select": ["price", "currency", "catalogGroupId"]})
    assert cache.saved is not None


@pytest.mark.asyncio
async def test_catalog_rejects_missing_configuration_and_bad_product() -> None:
    with pytest.raises(PaymentsNotConfiguredError):
        await PaymentProductCatalog(Settings(), Bitrix(), Cache()).list()
    settings = Settings(bx24_webhook="https://bx.test/rest/1/secret", bx24_payment_products={"Выезд": 123})
    with pytest.raises(Bitrix24Error, match="недоступен"):
        await PaymentProductCatalog(settings, Bitrix(active="N"), Cache()).list()


@pytest.mark.asyncio
async def test_catalog_uses_cached_safe_projection_without_bitrix_call() -> None:
    cached = [{"product_id": 123, "title": "Выезд", "default_amount": "99.00", "currency": "RUB", "price_override_allowed": True}]
    bitrix = Bitrix()
    settings = Settings(bx24_webhook="https://bx.test/rest/1/secret", bx24_payment_products={"Выезд": 123})
    products = await PaymentProductCatalog(settings, bitrix, Cache(cached)).list()
    assert products[0].default_amount == Decimal("99.00")
    assert bitrix.calls == []
