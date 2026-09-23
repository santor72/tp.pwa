"""User-authenticated photo upload into a TechPortal ticket."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ApiError
from app.techportal_client import TechPortalClient
from app.techportal_photo_delivery import PhotoSaveResult, PhotoUploadContext


class TechPortalPhotoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    tp_photo_upload_enabled: bool = False


class TechPortalPhotoAdapter:
    name = 'techportal'

    def __init__(self, client: TechPortalClient, settings: TechPortalPhotoSettings | None = None) -> None:
        self._client = client
        self._settings = settings if settings is not None else TechPortalPhotoSettings()

    @property
    def configured(self) -> bool:
        return self._settings.tp_photo_upload_enabled and self._client.user_api_available

    async def save_photo(self, key: str, content: bytes, content_type: str, *, filename: str,
                         context: PhotoUploadContext) -> PhotoSaveResult:
        if not self.configured:
            raise ApiError(503, 'TECHPORTAL_PHOTO_NOT_CONFIGURED', 'Загрузка фотографий в ТехПортал не настроена')
        await self._client.upload_ticket_file(
            context.ticket_id, filename, content, content_type, context.upstream_cookies,
        )
        # The upload endpoint attaches the file to the ticket; its response body is not needed.
        return PhotoSaveResult(comment_text='')
