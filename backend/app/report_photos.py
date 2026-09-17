"""Validation shared by reports that accept user-supplied photographs."""
from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError

from app.errors import ApiError

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_IMAGE_EDGE = 16_384
MAX_IMAGE_PIXELS = 50_000_000
_FORMATS = {
    'image/jpeg': ('JPEG', b'\xff\xd8\xff'),
    'image/png': ('PNG', b'\x89PNG\r\n\x1a\n'),
    'image/webp': ('WEBP', b'RIFF'),
}


def validate_report_photo(content_type: str, content: bytes, *, code_prefix: str = 'REPORT') -> None:
    expected = _FORMATS.get(content_type)
    if expected is None or not content.startswith(expected[1]):
        raise ApiError(422, f'{code_prefix}_PHOTO_TYPE', 'Допустимы фотографии JPEG, PNG или WebP')
    if len(content) > MAX_PHOTO_BYTES:
        raise ApiError(422, f'{code_prefix}_PHOTO_SIZE', 'Размер одной фотографии — не более 10 МБ')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                if image.format != expected[0] or image.width > MAX_IMAGE_EDGE or image.height > MAX_IMAGE_EDGE:
                    raise ValueError('unsupported image dimensions or format')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError('too many pixels')
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError) as exc:
        raise ApiError(422, f'{code_prefix}_PHOTO_CONTENT', 'Фотография повреждена или превышает допустимые размеры') from exc
