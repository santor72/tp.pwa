import pytest

from app.errors import ApiError
from app.techportal_photo_adapter import TechPortalPhotoAdapter, TechPortalPhotoSettings
from app.techportal_photo_delivery import PhotoUploadContext


class FakeClient:
    user_api_available = True

    def __init__(self) -> None:
        self.calls = []

    async def upload_ticket_file(self, ticket_id, filename, content, content_type, cookies):
        self.calls.append((ticket_id, filename, content, content_type, cookies))


@pytest.mark.asyncio
async def test_techportal_adapter_attaches_file_without_comment_text():
    client = FakeClient()
    adapter = TechPortalPhotoAdapter(client, TechPortalPhotoSettings(tp_photo_upload_enabled=True))
    context = PhotoUploadContext(ticket_id=54295, upstream_cookies={'tp-session': 'employee'})

    result = await adapter.save_photo('unused-key', b'PNG-data', 'image/png', filename='л1.png', context=context)

    assert result.comment_text == ''
    assert result.metadata == {}
    assert client.calls == [(54295, 'л1.png', b'PNG-data', 'image/png', {'tp-session': 'employee'})]


@pytest.mark.asyncio
async def test_techportal_adapter_is_opt_in():
    client = FakeClient()
    disabled = TechPortalPhotoAdapter(client, TechPortalPhotoSettings(tp_photo_upload_enabled=False))
    assert disabled.configured is False
    context = PhotoUploadContext(ticket_id=54295, upstream_cookies={})
    with pytest.raises(ApiError) as disabled_error:
        await disabled.save_photo('key', b'photo', 'image/png', filename='photo.png', context=context)
    assert disabled_error.value.code == 'TECHPORTAL_PHOTO_NOT_CONFIGURED'
