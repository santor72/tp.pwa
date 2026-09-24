from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from decimal import Decimal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "ТехПортал PWA API"
    log_level: str = "INFO"
    payment_telemetry_enabled: bool = False
    payment_processing_mode: Literal["legacy", "events"] = "legacy"
    payment_events_redis_url: str = "redis://redis:6379/3"
    payment_events_prefix: str = "tp-pwa"
    payment_outbox_poll_seconds: float = Field(default=1, gt=0, le=30)
    payment_recovery_interval_seconds: float = Field(default=30, gt=0)
    payment_lease_seconds: float = Field(default=90, gt=0)
    payment_heartbeat_seconds: float = Field(default=15, gt=0)
    payment_reclaim_idle_ms: int = Field(default=90000, gt=0)
    payment_shutdown_grace_seconds: float = Field(default=30, gt=0)
    payment_formation_concurrency: int = Field(default=1, ge=1, le=32)
    payment_reconciliation_concurrency: int = Field(default=1, ge=1, le=32)
    payment_bitrix_requests_per_second: float = Field(default=1, gt=0, le=100)
    payment_bitrix_cooldown_seconds: float = Field(default=30, gt=0)
    payment_stream_retention_enabled: bool = False
    payment_stream_retention_days: int = Field(default=30, ge=1)
    payment_stream_retention_batch_size: int = Field(default=50, ge=1, le=100)
    payment_stream_retention_interval_seconds: float = Field(default=600, gt=0)
    payment_job_retention_enabled: bool = False
    payment_job_retention_days: int = Field(default=30, ge=1)
    payment_job_retention_batch_size: int = Field(default=50, ge=1, le=100)
    payment_job_retention_interval_seconds: float = Field(default=600, gt=0)

    @model_validator(mode="after")
    def validate_payment_events(self):
        if self.payment_heartbeat_seconds * 3 > self.payment_lease_seconds:
            raise ValueError("Payment lease must cover at least three heartbeat intervals")
        prefix = self.payment_events_prefix
        if not prefix or len(prefix) > 64 or not all(c.isascii() and (c.isalnum() or c in '-_:') for c in prefix):
            raise ValueError("Invalid payment events prefix")
        return self

    tp_base_url: str = "https://tp.point.online"
    tp_base_token: str = ""
    tp_auth_timeout_seconds: float = 20.0
    tp_api_timeout_seconds: float = Field(default=20.0, gt=0)
    tp_tickets_max_pages: int = Field(default=100, gt=0)
    tp_users_cache_ttl_seconds: int = Field(default=300, gt=0)
    gis_base_url: str = ''
    gis_api_token: SecretStr = SecretStr('')
    yandex_maps_api_key: SecretStr = SecretStr('')
    gis_map_provider: Literal['yandex'] = 'yandex'
    gis_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    gis_icon_cache_dir: str = '/app/.cache/gis-icons'
    gis_icon_cache_ttl_seconds: int = Field(default=31_536_000, gt=0)
    gis_visible_techportal_roles: str = ''
    gis_tikets_visible_techportal_roles: str = ''
    gis_completion_worker_poll_seconds: float = Field(default=5.0, gt=0, le=300)
    gis_completion_worker_batch_size: int = Field(default=10, ge=1, le=100)

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_session_db: int = 0
    redis_cache_db: int = 1
    redis_bot_db: int = 2

    database_url: str = "postgresql+asyncpg://techportal:change-me@postgres:5432/techportal"
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    tg_access_groups: str = ""
    tg_access_cache_ttl_seconds: int = Field(default=300, gt=0)
    tg_link_token_ttl_seconds: int = Field(default=600, gt=0)
    messenger_show: bool = Field(default=False, validation_alias=AliasChoices("MESSENGER_SHOW", "MESSENDGER_SHOW"))
    https_proxy: str = ""

    session_cookie_name: str = "tp_pwa_session"
    session_cookie_secure: bool = True
    session_absolute_ttl_seconds: int = Field(default=43_200, gt=0)
    session_idle_ttl_seconds: int = Field(default=28_800, gt=0)

    esb_base_url: str = ""
    esb_base_token: str = ""
    esb_timeout_seconds: float = Field(default=20.0, gt=0)
    payment_addresses_cache_ttl_seconds: int = Field(
        default=300,
        gt=0,
        validation_alias=AliasChoices("PAYMENT_ADDRESSES_CACHE_TTL_SECONDS", "DOMOFON_ADDRESSES_CACHE_TTL_SECONDS"),
    )

    bx24_webhook: SecretStr = SecretStr("")
    bx24_payment_products: dict[str, int] = Field(default_factory=dict)
    bx24_price_group_id: int = Field(default=2, gt=0)
    bx24_new_lead_status_id: str = "NEW"
    bx24_payment_currency: str = "RUB"
    bx24_payment_min_amount: Decimal = Field(default=Decimal("1.00"), gt=0)
    bx24_payment_max_amount: Decimal = Field(default=Decimal("1000000.00"), gt=0)
    bx24_payment_allow_price_override: bool = True
    bx24_payment_link_field: str = ""
    bx24_payment_send_trigger: str = ""
    bx24_payment_create_activity: bool = False
    bx24_payment_webhook_token: SecretStr = SecretStr("")
    bx24_payment_poll_interval_seconds: int = Field(default=30, gt=0)
    bx24_payment_expires_seconds: int = Field(default=86400, gt=0)
    bx24_payment_catalog_cache_ttl_seconds: int = Field(default=300, gt=0)
    bx24_timeout_seconds: float = Field(default=20.0, gt=0)
    bx24_worker_batch_size: int = Field(default=10, gt=0, le=100)
    bx24_worker_max_retries: int = Field(default=8, ge=0, le=50)
    payment_health_interval_seconds: float = Field(default=30, gt=0)
    payment_health_due_age_seconds: float = Field(default=120, gt=0)
    payment_health_outbox_age_seconds: float = Field(default=10, gt=0)

    allowed_origins: str = "http://localhost:8080"

    @field_validator("esb_base_url")
    @classmethod
    def normalize_esb_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and "://" not in value:
            value = f"https://{value}"
        if value and urlsplit(value).scheme not in {"http", "https"}:
            raise ValueError("ESB_BASE_URL должен использовать http:// или https://")
        return value

    @field_validator('gis_base_url')
    @classmethod
    def normalize_gis_base_url(cls, value: str) -> str:
        value = value.strip().rstrip('/')
        if value and '://' not in value:
            value = f'https://{value}'
        if value and urlsplit(value).scheme not in {'http', 'https'}:
            raise ValueError('GIS_BASE_URL должен использовать http:// или https://')
        return value

    @field_validator("bx24_webhook")
    @classmethod
    def validate_bx24_webhook(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip().rstrip("/")
        if raw:
            parsed = urlsplit(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("BX24_WEBHOOK должен быть корректным HTTP(S) URL")
        return SecretStr(raw)

    @field_validator("bx24_payment_products")
    @classmethod
    def validate_payment_products(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for title, product_id in value.items():
            clean_title = title.strip()
            if not clean_title or isinstance(product_id, bool) or product_id <= 0:
                raise ValueError("BX24_PAYMENT_PRODUCTS должен содержать названия и положительные ID")
            if clean_title in normalized:
                raise ValueError("Названия товаров в BX24_PAYMENT_PRODUCTS не должны повторяться")
            normalized[clean_title] = product_id
        return normalized

    @field_validator("bx24_new_lead_status_id", "bx24_payment_currency")
    @classmethod
    def strip_required_payment_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Значение настройки платежей не может быть пустым")
        return value

    @model_validator(mode="after")
    def validate_payment_amount_range(self) -> "Settings":
        if self.bx24_payment_min_amount > self.bx24_payment_max_amount:
            raise ValueError("BX24_PAYMENT_MIN_AMOUNT не может быть больше максимальной суммы")
        return self

    @property
    def bx24_webhook_url(self) -> str:
        return self.bx24_webhook.get_secret_value()

    @property
    def session_redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_session_db}"

    @property
    def cache_redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_cache_db}"

    @property
    def bot_redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_bot_db}"

    @property
    def telegram_access_group_ids(self) -> tuple[int, ...]:
        values = tuple(item.strip() for item in self.tg_access_groups.split(",") if item.strip())
        if not values:
            return ()
        try:
            return tuple(int(value) for value in values)
        except ValueError as exc:
            raise ValueError("TG_ACCESS_GROUPS должен содержать числовые chat ID") from exc

    @field_validator("https_proxy")
    @classmethod
    def validate_https_proxy(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname:
            raise ValueError("HTTPS_PROXY должен быть URL прокси")
        return value

    @field_validator("telegram_bot_username")
    @classmethod
    def validate_telegram_bot_username(cls, value: str) -> str:
        value = value.strip().removeprefix("@")
        if value and not value.replace("_", "").isalnum():
            raise ValueError("TELEGRAM_BOT_USERNAME должен быть username бота без @")
        return value

    @property
    def tp_origin_url(self) -> str:
        parsed = urlsplit(self.tp_base_url)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.tp_base_url.rstrip("/")

    @property
    def origin_set(self) -> set[str]:
        return {item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
