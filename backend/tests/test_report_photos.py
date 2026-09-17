from io import BytesIO

import pytest
from PIL import Image

from app.errors import ApiError
from app.report_photos import validate_report_photo


def image_bytes(image_format: str = 'JPEG') -> bytes:
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color='white').save(buffer, format=image_format)
    return buffer.getvalue()


def test_report_photo_accepts_valid_declared_image():
    validate_report_photo('image/jpeg', image_bytes())


@pytest.mark.parametrize('content_type,content', [
    ('image/jpeg', b'\xff\xd8\xffnot-an-image'),
    ('image/png', image_bytes()),
])
def test_report_photo_rejects_forged_or_mismatched_image(content_type, content):
    with pytest.raises(ApiError) as error:
        validate_report_photo(content_type, content)
    assert error.value.status_code == 422
