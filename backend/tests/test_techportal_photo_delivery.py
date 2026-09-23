from uuid import uuid4

import pytest

from app.errors import ApiError
from app.techportal_photo_delivery import PhotoSaveResult, PhotoUploadContext, TechPortalPhotoDelivery

CONTEXT = PhotoUploadContext(ticket_id=12, upstream_cookies={})


class FakeAdapter:
    def __init__(self, name: str, calls: list, *, configured: bool = True, fail: bool = False) -> None:
        self.name = name
        self.configured = configured
        self.calls = calls
        self.fail = fail

    async def save_photo(self, key: str, content: bytes, content_type: str, *, filename: str,
                         context: PhotoUploadContext) -> PhotoSaveResult:
        self.calls.append((self.name, content, content_type))
        if self.fail:
            raise ApiError(503, 'UPLOAD_FAILED', 'Не удалось сохранить фото')
        return PhotoSaveResult(comment_text=f'{self.name}:{content.decode()}', metadata={'key': key})


@pytest.mark.asyncio
async def test_delivery_calls_each_configured_adapter_in_order_and_collects_comment_text():
    calls = []
    delivery = TechPortalPhotoDelivery([
        FakeAdapter('first', calls), FakeAdapter('disabled', calls, configured=False), FakeAdapter('second', calls),
    ])
    photos = [
        {'name': 'a.jpg', 'content_type': 'image/jpeg', 'content': b'a'},
        {'name': 'b.jpg', 'content_type': 'image/jpeg', 'content': b'b'},
    ]

    saved = await delivery.save(uuid4(), photos, context=CONTEXT)

    assert delivery.available is True
    assert calls == [('first', b'a', 'image/jpeg'), ('first', b'b', 'image/jpeg'),
                     ('second', b'a', 'image/jpeg'), ('second', b'b', 'image/jpeg')]
    assert TechPortalPhotoDelivery.comment_texts(saved) == ['first:a', 'second:a', 'first:b', 'second:b']
    assert [copy['adapter'] for copy in saved[0]['copies']] == ['first', 'second']


@pytest.mark.asyncio
async def test_delivery_requires_one_configured_adapter_and_stops_on_failure():
    calls = []
    disabled = TechPortalPhotoDelivery([FakeAdapter('disabled', calls, configured=False)])
    assert disabled.available is False
    with pytest.raises(ApiError, match='Загрузка фотографий для заявок не настроена'):
        await disabled.save(uuid4(), [{'name': 'a.jpg', 'content_type': 'image/jpeg', 'content': b'a'}], context=CONTEXT)

    delivery = TechPortalPhotoDelivery([
        FakeAdapter('first', calls), FakeAdapter('failed', calls, fail=True), FakeAdapter('last', calls),
    ])
    with pytest.raises(ApiError) as error:
        await delivery.save(uuid4(), [{'name': 'a.jpg', 'content_type': 'image/jpeg', 'content': b'a'}], context=CONTEXT)
    assert error.value.code == 'UPLOAD_FAILED'
    assert [name for name, _, _ in calls] == ['first', 'failed']
