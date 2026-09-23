import pytest
from pydantic import ValidationError

from app.errors import ApiError
from app.object_storage import ObjectStorage, S3PhotoSettings
from app.techportal_photo_delivery import PhotoUploadContext


class FakeS3:
    def __init__(self) -> None:
        self.objects = {}

    def put_object(self, **values):
        self.objects[(values['Bucket'], values['Key'])] = values

    def get_object(self, **values):
        class Body:
            def __init__(self, content): self._content = content
            def read(self): return self._content
        return {'Body': Body(self.objects[(values['Bucket'], values['Key'])]['Body'])}


def configured() -> S3PhotoSettings:
    return S3PhotoSettings(
        s3_endpoint_url='http://seaweedfs:8333', s3_access_key_id='access', s3_secret_access_key='secret',
        s3_bucket='reports', s3_public_base_url='https://files.example/reports',
    )


def test_s3_endpoint_accepts_http_and_https() -> None:
    assert S3PhotoSettings().s3_photo_usage == 'gis_and_techportal'
    assert S3PhotoSettings(s3_endpoint_url='http://seaweedfs:8333').s3_endpoint_url == 'http://seaweedfs:8333'
    assert S3PhotoSettings(s3_endpoint_url='https://s3.example').s3_endpoint_url == 'https://s3.example'
    with pytest.raises(ValidationError, match='S3 URL должен использовать'):
        S3PhotoSettings(s3_endpoint_url='ftp://s3.example')
    with pytest.raises(ValidationError):
        S3PhotoSettings(s3_photo_usage='invalid')


def test_storage_uses_path_style_for_internal_seaweedfs_endpoint():
    client = ObjectStorage(configured())._s3()

    assert client.meta.config.s3['addressing_style'] == 'path'


@pytest.mark.asyncio
async def test_storage_keeps_private_s3_key_and_returns_configured_public_url():
    storage = ObjectStorage(configured())
    storage._client = FakeS3()

    public_url = await storage.put('completion/id/photo 1.jpg', b'photo', 'image/jpeg')

    assert public_url == 'https://files.example/reports/completion/id/photo%201.jpg'
    assert await storage.get('completion/id/photo 1.jpg') == b'photo'
    assert storage._client.objects[('reports', 'completion/id/photo 1.jpg')]['ContentType'] == 'image/jpeg'


@pytest.mark.asyncio
async def test_storage_rejects_completion_photos_without_full_s3_configuration():
    with pytest.raises(ApiError) as error:
        await ObjectStorage(S3PhotoSettings()).put('completion/id/photo.jpg', b'photo', 'image/jpeg')
    assert error.value.code == 'S3_NOT_CONFIGURED'


@pytest.mark.asyncio
async def test_gis_only_storage_does_not_require_public_url_or_return_comment_text():
    storage = ObjectStorage(S3PhotoSettings(
        s3_endpoint_url='http://seaweedfs:8333', s3_access_key_id='access', s3_secret_access_key='secret',
        s3_bucket='reports', s3_photo_usage='gis_only',
    ))
    storage._client = FakeS3()

    assert storage.configured is True
    assert storage.techportal_enabled is False
    assert await storage.stage_for_gis('completion/id/photo.jpg', b'photo', 'image/jpeg') == {'key': 'completion/id/photo.jpg'}
    assert await storage.get('completion/id/photo.jpg') == b'photo'
    with pytest.raises(ApiError):
        await storage.save_photo('completion/id/other.jpg', b'photo', 'image/jpeg', filename='other.jpg',
                                 context=PhotoUploadContext(ticket_id=12, upstream_cookies={}))
