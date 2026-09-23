"""Small S3 adapter for SeaweedFS; public URLs are deliberately configured separately."""
import asyncio
from typing import Literal
from urllib.parse import quote, urlsplit

import boto3
from botocore.config import Config
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ApiError, ServiceUnavailableError
from app.techportal_photo_delivery import PhotoSaveResult, PhotoUploadContext


class S3PhotoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    s3_endpoint_url: str = ''
    s3_access_key_id: str = ''
    s3_secret_access_key: SecretStr = SecretStr('')
    s3_bucket: str = ''
    s3_region: str = 'us-east-1'
    s3_public_base_url: str = ''
    s3_photo_usage: Literal['gis_only', 'gis_and_techportal'] = 'gis_and_techportal'
    s3_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    @field_validator('s3_endpoint_url', 's3_public_base_url')
    @classmethod
    def normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip('/')
        if value and urlsplit(value).scheme not in {'http', 'https'}:
            raise ValueError('S3 URL должен использовать http:// или https://')
        return value


class ObjectStorage:
    name = 's3'

    def __init__(self, settings: S3PhotoSettings | None = None) -> None:
        self._settings = settings or S3PhotoSettings()
        self._client = None

    @property
    def configured(self) -> bool:
        return all((self._settings.s3_endpoint_url, self._settings.s3_access_key_id,
                    self._settings.s3_secret_access_key.get_secret_value(), self._settings.s3_bucket))

    @property
    def techportal_enabled(self) -> bool:
        return (self.configured and self._settings.s3_photo_usage == 'gis_and_techportal'
                and bool(self._settings.s3_public_base_url))

    def _configured(self) -> None:
        if not self.configured:
            raise ApiError(503, 'S3_NOT_CONFIGURED', 'Хранилище фотографий не настроено')

    async def save_photo(self, key: str, content: bytes, content_type: str, *,
                         filename: str, context: PhotoUploadContext) -> PhotoSaveResult:
        if not self.techportal_enabled:
            raise ApiError(503, 'S3_NOT_CONFIGURED', 'S3-адаптер фотографий ТехПортала не настроен')
        public_url = await self.put(key, content, content_type)
        return PhotoSaveResult(comment_text=public_url, metadata={'key': key, 'public_url': public_url})

    async def stage_for_gis(self, key: str, content: bytes, content_type: str) -> dict[str, str]:
        await self._put_object(key, content, content_type)
        return {'key': key}

    def _s3(self):
        self._configured()
        if self._client is None:
            self._client = boto3.client(
                's3', endpoint_url=self._settings.s3_endpoint_url,
                aws_access_key_id=self._settings.s3_access_key_id,
                aws_secret_access_key=self._settings.s3_secret_access_key.get_secret_value(),
                region_name=self._settings.s3_region,
                config=Config(signature_version='s3v4', connect_timeout=self._settings.s3_timeout_seconds,
                              read_timeout=self._settings.s3_timeout_seconds, retries={'max_attempts': 1},
                              s3={'addressing_style': 'path'}),
            )
        return self._client

    async def put(self, key: str, content: bytes, content_type: str) -> str:
        await self._put_object(key, content, content_type)
        return self.public_url(key)

    async def _put_object(self, key: str, content: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(self._s3().put_object, Bucket=self._settings.s3_bucket, Key=key,
                                    Body=content, ContentType=content_type)
        except ApiError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError('Не удалось сохранить фотографию') from exc

    async def get(self, key: str) -> bytes:
        try:
            result = await asyncio.to_thread(self._s3().get_object, Bucket=self._settings.s3_bucket, Key=key)
            return await asyncio.to_thread(result['Body'].read)
        except ApiError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError('Не удалось получить фотографию') from exc

    def public_url(self, key: str) -> str:
        self._configured()
        if not self._settings.s3_public_base_url:
            raise ApiError(503, 'S3_NOT_CONFIGURED', 'Публичный адрес S3 не настроен')
        return f"{self._settings.s3_public_base_url}/{quote(key, safe='/')}"
