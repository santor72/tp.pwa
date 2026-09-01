from decimal import Decimal, InvalidOperation

from app.bitrix24_client import Bitrix24Client
from app.cache_store import CacheStore
from app.config import Settings
from app.errors import Bitrix24Error, PaymentsNotConfiguredError
from app.schemas import PaymentProduct


class PaymentProductCatalog:
    cache_key = "payments:catalog:v1"

    def __init__(self, settings: Settings, bitrix: Bitrix24Client, cache: CacheStore) -> None:
        self._settings = settings
        self._bitrix = bitrix
        self._cache = cache

    async def list(self) -> list[PaymentProduct]:
        if not self._settings.bx24_webhook_url or not self._settings.bx24_payment_products:
            raise PaymentsNotConfiguredError()
        cached = await self._cache.get_json(self.cache_key)
        if isinstance(cached, list):
            return [PaymentProduct.model_validate(item) for item in cached]
        products = [await self._load(title, product_id) for title, product_id in self._settings.bx24_payment_products.items()]
        await self._cache.set_json(self.cache_key, [item.model_dump(mode="json") for item in products], self._settings.bx24_payment_catalog_cache_ttl_seconds)
        return products

    async def get(self, product_id: int) -> PaymentProduct:
        for product in await self.list():
            if product.product_id == product_id:
                return product
        raise Bitrix24Error("BX24_PRODUCT_NOT_ALLOWED", "Товар недоступен для оплаты", 422)

    async def _load(self, title: str, product_id: int) -> PaymentProduct:
        product_result = await self._bitrix.call("catalog.product.get", {"id": product_id})
        product = product_result.get("product") if isinstance(product_result, dict) else None
        if not isinstance(product, dict) or product.get("active", product.get("ACTIVE", "Y")) in {False, "N"}:
            raise Bitrix24Error("BX24_PRODUCT_UNAVAILABLE", "Настроенный товар недоступен")
        prices_result = await self._bitrix.call("catalog.price.list", {
            "filter": {"productId": product_id, "catalogGroupId": self._settings.bx24_price_group_id},
            "select": ["price", "currency", "catalogGroupId"],
        })
        prices = prices_result.get("prices", prices_result.get("items", [])) if isinstance(prices_result, dict) else []
        if not isinstance(prices, list) or not prices:
            raise Bitrix24Error("BX24_PRODUCT_PRICE_UNAVAILABLE", "Для товара не настроена цена")
        raw_price = prices[0].get("price", prices[0].get("PRICE"))
        try:
            price = Decimal(str(raw_price)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError) as exc:
            raise Bitrix24Error("BX24_PRODUCT_PRICE_UNAVAILABLE", "Битрикс24 вернул некорректную цену") from exc
        if price <= 0:
            raise Bitrix24Error("BX24_PRODUCT_PRICE_UNAVAILABLE", "Для товара не настроена цена")
        currency = str(prices[0].get("currency", prices[0].get("CURRENCY", self._settings.bx24_payment_currency))).upper()
        if currency != self._settings.bx24_payment_currency.upper():
            raise Bitrix24Error("BX24_PRODUCT_CURRENCY_INVALID", "Валюта товара не поддерживается")
        return PaymentProduct(product_id=product_id, title=title, default_amount=price, currency=currency, price_override_allowed=self._settings.bx24_payment_allow_price_override)
