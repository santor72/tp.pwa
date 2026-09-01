from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_payment_configuration_parses_products_and_redacts_secrets() -> None:
    settings = Settings(
        bx24_webhook="https://example.bitrix24.ru/rest/1/secret/",
        bx24_payment_products={" Подключение ": 123},
        bx24_payment_webhook_token="callback-secret",
    )
    assert settings.bx24_webhook_url == "https://example.bitrix24.ru/rest/1/secret"
    assert settings.bx24_payment_products == {"Подключение": 123}
    assert "callback-secret" not in repr(settings)
    assert "/secret" not in repr(settings)


@pytest.mark.parametrize("products", [{"": 1}, {"Товар": 0}, []])
def test_payment_configuration_rejects_invalid_product_mapping(products: object) -> None:
    with pytest.raises(ValidationError):
        Settings(bx24_payment_products=products)


def test_payment_amount_limits_are_decimal() -> None:
    settings = Settings(bx24_payment_min_amount="10.25", bx24_payment_max_amount="500.50")
    assert settings.bx24_payment_min_amount == Decimal("10.25")
    assert settings.bx24_payment_max_amount == Decimal("500.50")
    with pytest.raises(ValidationError):
        Settings(bx24_payment_min_amount="500.50", bx24_payment_max_amount="10.25")
