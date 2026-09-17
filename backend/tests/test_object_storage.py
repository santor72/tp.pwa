import pytest

from app.config import Settings
from app.errors import ApiError
from app.object_storage import ObjectStorage


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


def configured() -> Settings:
    return Settings(
        s3_endpoint_url='http://seaweedfs:8333', s3_access_key_id='access', s3_secret_access_key='secret',
        s3_bucket='reports', s3_public_base_url='https://files.example/reports',
    )


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
        await ObjectStorage(Settings()).put('completion/id/photo.jpg', b'photo', 'image/jpeg')
    assert error.value.code == 'S3_NOT_CONFIGURED'
