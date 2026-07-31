from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ТехПортал PWA API"
    log_level: str = "INFO"

    tp_base_url: str = "https://tp.point.online"
    tp_base_token: str = ""
    tp_auth_timeout_seconds: float = 20.0
    tp_api_timeout_seconds: float = Field(default=20.0, gt=0)
    tp_users_cache_ttl_seconds: int = Field(default=300, gt=0)

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
    https_proxy: str = ""

    session_cookie_name: str = "tp_pwa_session"
    session_cookie_secure: bool = True
    session_absolute_ttl_seconds: int = Field(default=43_200, gt=0)
    session_idle_ttl_seconds: int = Field(default=28_800, gt=0)

    esb_base_url: str = ""
    esb_base_token: str = ""
    esb_timeout_seconds: float = Field(default=20.0, gt=0)
    domofon_addresses_cache_ttl_seconds: int = Field(default=300, gt=0)

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
