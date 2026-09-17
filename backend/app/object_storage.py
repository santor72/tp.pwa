"""Small S3 adapter for SeaweedFS; public URLs are deliberately configured separately."""
import asyncio
from urllib.parse import quote

import boto3
from botocore.config import Config

from app.config import Settings
from app.errors import ApiError, ServiceUnavailableError


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _configured(self) -> None:
        if not all((self._settings.s3_endpoint_url, self._settings.s3_access_key_id,
                    self._settings.s3_secret_access_key.get_secret_value(), self._settings.s3_bucket,
                    self._settings.s3_public_base_url)):
            raise ApiError(503, 'S3_NOT_CONFIGURED', 'Хранилище фотографий не настроено')

    def _s3(self):
        self._configured()
        if self._client is None:
            self._client = boto3.client(
                's3', endpoint_url=self._settings.s3_endpoint_url,
                aws_access_key_id=self._settings.s3_access_key_id,
                aws_secret_access_key=self._settings.s3_secret_access_key.get_secret_value(),
                region_name=self._settings.s3_region,
                config=Config(signature_version='s3v4', connect_timeout=self._settings.s3_timeout_seconds,
                              read_timeout=self._settings.s3_timeout_seconds, retries={'max_attempts': 1}),
            )
        return self._client

    async def put(self, key: str, content: bytes, content_type: str) -> str:
        try:
            await asyncio.to_thread(self._s3().put_object, Bucket=self._settings.s3_bucket, Key=key,
                                    Body=content, ContentType=content_type)
        except ApiError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError('Не удалось сохранить фотографию') from exc
        return self.public_url(key)

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
        return f"{self._settings.s3_public_base_url}/{quote(key, safe='/')}"
