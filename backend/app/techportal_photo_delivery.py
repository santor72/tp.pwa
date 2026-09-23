"""Send report photos through every configured storage adapter."""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.errors import ApiError


@dataclass(frozen=True)
class PhotoSaveResult:
    comment_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhotoUploadContext:
    ticket_id: int
    upstream_cookies: dict[str, str]


class PhotoStorageAdapter(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def save_photo(self, key: str, content: bytes, content_type: str, *,
                         filename: str, context: PhotoUploadContext) -> PhotoSaveResult: ...


class TechPortalPhotoDelivery:
    def __init__(self, adapters: list[PhotoStorageAdapter]) -> None:
        self._adapters = adapters

    @property
    def available(self) -> bool:
        return any(self._enabled(adapter) for adapter in self._adapters)

    def includes(self, adapter: PhotoStorageAdapter) -> bool:
        return any(candidate is adapter and self._enabled(candidate) for candidate in self._adapters)

    @staticmethod
    def _enabled(adapter: PhotoStorageAdapter) -> bool:
        return bool(getattr(adapter, 'techportal_enabled', adapter.configured))

    async def save(self, operation_id: UUID, photos: list[dict[str, Any]], *, context: PhotoUploadContext,
                   allow_no_adapters: bool = False) -> list[dict[str, Any]]:
        adapters = [adapter for adapter in self._adapters if self._enabled(adapter)]
        if photos and not adapters and not allow_no_adapters:
            raise ApiError(503, 'PHOTO_STORAGE_NOT_CONFIGURED', 'Загрузка фотографий для заявок не настроена')

        saved = [
            {
                'name': photo['name'], 'content_type': photo['content_type'],
                'size': len(photo['content']), 'sha256': hashlib.sha256(photo['content']).hexdigest(),
                'copies': [],
            }
            for photo in photos
        ]
        for adapter in adapters:
            for index, photo in enumerate(photos):
                key = f'completion/{operation_id}/{index + 1}-{uuid4().hex}'
                result = await adapter.save_photo(key, photo['content'], photo['content_type'],
                                                  filename=photo['name'], context=context)
                copy = {**result.metadata, 'adapter': adapter.name, 'comment_text': result.comment_text}
                saved[index]['copies'].append(copy)
                # Preserve the top-level key for existing consumers; copies identify each adapter.
                if 'key' not in saved[index] and 'key' in result.metadata:
                    saved[index]['key'] = result.metadata['key']
        return saved

    @staticmethod
    def comment_texts(saved: list[dict[str, Any]]) -> list[str]:
        return [copy['comment_text'] for photo in saved for copy in photo['copies'] if copy['comment_text']]
