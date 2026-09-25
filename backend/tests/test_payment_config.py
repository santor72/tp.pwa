from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_payment_configuration_parses_products_and_redacts_secrets() -> None:
    settings = Settings(_env_file=None,
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
        Settings(_env_file=None, bx24_payment_products=products)


def test_payment_amount_limits_are_decimal() -> None:
    settings = Settings(_env_file=None, bx24_payment_min_amount="10.25", bx24_payment_max_amount="500.50")
    assert settings.bx24_payment_min_amount == Decimal("10.25")
    assert settings.bx24_payment_max_amount == Decimal("500.50")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bx24_payment_min_amount="500.50", bx24_payment_max_amount="10.25")


def test_gis_map_provider_is_explicit_and_limited_to_registered_implementations() -> None:
    assert Settings(_env_file=None).gis_map_provider == 'yandex'
    assert Settings(_env_file=None, gis_map_provider='yandex-v3').gis_map_provider == 'yandex-v3'
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gis_map_provider='2gis')


def test_event_runtime_settings_are_complete_and_validate_lease_heartbeat_relation() -> None:
    settings = Settings(_env_file=None)
    assert settings.payment_processing_mode == 'legacy'
    assert settings.payment_events_redis_url.endswith('/3')
    assert settings.payment_outbox_poll_seconds > 0
    assert settings.payment_recovery_interval_seconds > 0
    assert settings.payment_reclaim_idle_ms > 0
    assert settings.payment_formation_concurrency >= 1
    assert settings.payment_reconciliation_concurrency >= 1
    assert settings.payment_bitrix_requests_per_second > 0
    assert settings.payment_shutdown_grace_seconds > 0
    assert not settings.payment_stream_retention_enabled
    assert not settings.payment_job_retention_enabled
    with pytest.raises(ValidationError, match='three heartbeat'):
        Settings(_env_file=None, payment_lease_seconds=29, payment_heartbeat_seconds=10)
    with pytest.raises(ValidationError, match='Invalid payment events prefix'):
        Settings(_env_file=None, payment_events_prefix='not allowed space')
