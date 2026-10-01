from io import BytesIO
from pathlib import Path
import struct
import zlib

import pytest
from fastapi import UploadFile
from PIL import Image, ImageCms, PngImagePlugin, TiffImagePlugin

from app.errors import ApiError
from app import report_photos
from app.report_photos import normalize_report_photo, prepare_report_photos


def image_bytes(image_format: str = 'JPEG', *, size=(20, 10), mode='RGB', color='red', **options) -> bytes:
    buffer = BytesIO()
    Image.new(mode, size, color=color).save(buffer, format=image_format, **options)
    return buffer.getvalue()


@pytest.mark.parametrize('image_format', ['JPEG', 'PNG', 'WEBP', 'HEIF', 'AVIF', 'TIFF', 'BMP', 'GIF'])
def test_supported_rasters_are_reencoded_as_srgb_jpeg(image_format):
    content = image_bytes(image_format)
    result = normalize_report_photo(content, filename=r'folder\misnamed.jpeg')
    assert result['name'] == 'misnamed.jpg'
    assert result['content_type'] == 'image/jpeg'
    with Image.open(BytesIO(result['content'])) as image:
        image.load()
        assert image.format == 'JPEG'
        assert image.size == (20, 10)
        assert image.mode == 'RGB'
        assert 'sRGB' in ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(BytesIO(image.info['icc_profile'])))
        assert image.getpixel((10, 5))[0] > 240


def test_real_iphone_heic_is_read():
    # macOS/iPhone fixture supplied by the GIS project, tests/photo-fixtures.ts.
    content = (Path(__file__).parent / 'fixtures' / 'iphone.heic').read_bytes()
    result = normalize_report_photo(content, filename='phone.heic')
    with Image.open(BytesIO(result['content'])) as image:
        image.load()
        assert image.format == 'JPEG'
        assert image.size == (56, 56)


def test_heif_primary_image_is_used_instead_of_first_image():
    output = BytesIO()
    Image.new('RGB', (8, 8), 'red').save(
        output, format='HEIF', save_all=True, primary_index=1,
        append_images=[Image.new('RGB', (20, 10), 'blue')],
    )
    result = normalize_report_photo(output.getvalue())
    with Image.open(BytesIO(result['content'])) as image:
        assert image.size == (20, 10)
        assert image.getpixel((10, 5))[2] > 240


@pytest.mark.parametrize('image_format', ['GIF', 'TIFF', 'WEBP', 'PNG', 'AVIF'])
def test_animated_and_multipage_files_use_first_frame(image_format):
    output = BytesIO()
    Image.new('RGB', (20, 10), 'red').save(
        output, format=image_format, save_all=True,
        append_images=[Image.new('RGB', (20, 10), 'blue')],
    )
    result = normalize_report_photo(output.getvalue())
    with Image.open(BytesIO(result['content'])) as image:
        assert image.getpixel((10, 5))[0] > 240


def test_large_rotated_photo_is_upright_and_resized_without_metadata():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = 'private source metadata'
    content = image_bytes(size=(5000, 2500), exif=exif)
    result = normalize_report_photo(content)
    with Image.open(BytesIO(result['content'])) as image:
        assert image.size == (2048, 4096)
        assert not image.getexif()


def test_small_photo_is_not_enlarged_and_trailing_data_is_removed():
    result = normalize_report_photo(image_bytes() + b'APPENDED PRIVATE PAYLOAD')
    assert b'APPENDED PRIVATE PAYLOAD' not in result['content']
    with Image.open(BytesIO(result['content'])) as image:
        assert image.size == (20, 10)


def test_prepared_profile_has_a_stable_timestamp_for_retries():
    prepared = normalize_report_photo(image_bytes())
    with Image.open(BytesIO(prepared['content'])) as image:
        assert struct.unpack('>6H', image.info['icc_profile'][24:36]) == (2000, 1, 1, 0, 0, 0)
    assert normalize_report_photo(image_bytes())['content'] == prepared['content']


def test_output_is_resized_further_if_jpeg_exceeds_gis_limit(monkeypatch):
    monkeypatch.setattr(report_photos, 'MAX_PHOTO_BYTES', 30_000)
    # Deterministic detailed image that compresses poorly.
    import random
    image = Image.frombytes('RGB', (512, 512), random.Random(7).randbytes(512 * 512 * 3))
    output = BytesIO()
    image.save(output, format='PNG')
    prepared = normalize_report_photo(output.getvalue())
    assert len(prepared['content']) <= 30_000
    with Image.open(BytesIO(prepared['content'])) as saved:
        assert max(saved.size) < 512


@pytest.mark.parametrize('mode,color', [('RGBA', (255, 0, 0, 0)), ('LA', (0, 0)), ('P', 0)])
def test_transparency_is_composited_on_white(mode, color):
    options = {'transparency': 0} if mode == 'P' else {}
    result = normalize_report_photo(image_bytes('PNG', mode=mode, color=color, **options))
    with Image.open(BytesIO(result['content'])) as image:
        assert min(image.getpixel((10, 5))) > 250


def test_embedded_lab_profile_is_converted_to_srgb():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('LAB'))
    image = Image.new('RGB', (20, 10), 'red')
    image = ImageCms.profileToProfile(image, ImageCms.createProfile('sRGB'), profile, outputMode='LAB')
    output = BytesIO()
    image.save(output, format='TIFF', icc_profile=profile.tobytes())
    result = normalize_report_photo(output.getvalue())
    with Image.open(BytesIO(result['content'])) as prepared:
        red, green, blue = prepared.getpixel((10, 5))
        assert red > 230 and green < 30 and blue < 30


def test_cmyk_jpeg_without_profile_is_supported():
    result = normalize_report_photo(image_bytes(mode='CMYK', color=(0, 255, 255, 0)))
    with Image.open(BytesIO(result['content'])) as image:
        assert image.mode == 'RGB'
        assert image.getpixel((10, 5))[0] > 240


def test_16bit_grayscale_tiff_is_scaled_to_8bit():
    result = normalize_report_photo(image_bytes('TIFF', mode='I;16', color=32768))
    with Image.open(BytesIO(result['content'])) as image:
        assert 120 <= image.getpixel((10, 5))[0] <= 135


def test_invalid_profile_gets_specific_error():
    with pytest.raises(ApiError) as error:
        normalize_report_photo(image_bytes('PNG', icc_profile=b'invalid ICC'), label='Фото 2')
    assert error.value.code == 'REPORT_PHOTO_COLOR_PROFILE'
    assert error.value.message.startswith('Фото 2:')


@pytest.mark.parametrize('content,reason', [
    (b'%PDF-1.7 document', 'PDF'),
    (b'<?xml version="1.0"?><svg/>', 'SVG'),
    (struct.pack('>I', 24) + b'ftypisom' + b'\x00' * 12, 'Видео'),
    (b'RIFF' + b'\x00' * 4 + b'AVI ' + b'\x00' * 20, 'Видео'),
    (b'FUJIFILMCCD-RAW' + b'\x00' * 30, 'RAW'),
    (struct.pack('>I', 24) + b'ftypcrx ' + b'\x00' * 12, 'RAW'),
])
def test_documents_video_and_raw_are_rejected_by_content(content, reason):
    with pytest.raises(ApiError) as error:
        normalize_report_photo(content, filename='photo.jpeg', label='Фото 3')
    assert error.value.code == 'REPORT_PHOTO_TYPE'
    assert reason in error.value.message
    assert error.value.message.startswith('Фото 3:')


def test_dng_tiff_container_is_rejected():
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[50706] = (1, 4, 0, 0)
    with pytest.raises(ApiError) as error:
        normalize_report_photo(image_bytes('TIFF', tiffinfo=tags))
    assert error.value.code == 'REPORT_PHOTO_TYPE'
    assert 'RAW' in error.value.message


def test_raw_in_tiff_subdirectory_is_rejected_even_with_preview():
    # Root SubIFDs tag points to a directory containing a DNG marker.
    content = b'II*\0' + struct.pack('<I', 8)
    content += struct.pack('<HHHII', 1, 330, 4, 1, 26) + struct.pack('<I', 0)
    content += struct.pack('<HHHII', 1, 50706, 1, 4, 0) + struct.pack('<I', 0)
    with pytest.raises(ApiError) as error:
        normalize_report_photo(content)
    assert error.value.code == 'REPORT_PHOTO_TYPE'


def test_svg_text_in_png_metadata_does_not_make_a_png_an_svg():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('comment', '<svg/>')
    result = normalize_report_photo(image_bytes('PNG', pnginfo=metadata))
    assert result['content_type'] == 'image/jpeg'


@pytest.mark.parametrize('content', [b'', b'\xff\xd8\xffnot-an-image', image_bytes()[:200], b'\x00\x00\x00\x18ftypheic' + b'\0' * 40])
def test_corrupt_images_are_rejected(content):
    with pytest.raises(ApiError) as error:
        normalize_report_photo(content, code_prefix='GIS_REPORT')
    assert error.value.code == 'GIS_REPORT_PHOTO_CONTENT'
    assert error.value.status_code == 422


def test_input_size_is_checked_before_decoding():
    with pytest.raises(ApiError) as error:
        normalize_report_photo(b'x' * (25 * 1024 * 1024 + 1))
    assert error.value.code == 'REPORT_PHOTO_SIZE'


def test_pixel_limit_is_checked_before_loading(monkeypatch):
    content = bytearray(image_bytes('PNG'))
    content[16:24] = struct.pack('>II', 20_000, 20_000)
    content[29:33] = struct.pack('>I', zlib.crc32(content[12:29]))
    monkeypatch.setattr(Image.Image, 'load', lambda _: pytest.fail('oversized image must not be decoded'))
    with pytest.raises(ApiError) as error:
        normalize_report_photo(bytes(content))
    assert error.value.code == 'REPORT_PHOTO_DIMENSIONS'


@pytest.mark.asyncio
async def test_uploads_ignore_declared_mime_and_extensions():
    from starlette.datastructures import Headers
    photos = [UploadFile(BytesIO(image_bytes('PNG')), filename='wrong.heic', headers=Headers({'content-type': 'application/octet-stream'}))]
    prepared = await prepare_report_photos(photos)
    assert prepared[0]['name'] == 'wrong.jpg'
    assert prepared[0]['content_type'] == 'image/jpeg'


@pytest.mark.asyncio
async def test_total_intake_limit_counts_source_bytes(monkeypatch):
    monkeypatch.setattr(report_photos, 'MAX_REPORT_PHOTO_BYTES', 20)
    monkeypatch.setattr(report_photos, 'normalize_report_photo', lambda *args, **kwargs: {'content': b'x'})
    photos = [UploadFile(BytesIO(b'x' * 10), filename=f'{index}.png') for index in range(3)]
    with pytest.raises(ApiError) as error:
        await prepare_report_photos(photos)
    assert error.value.code == 'REPORT_SIZE_LIMIT'


@pytest.mark.asyncio
async def test_photo_count_limit():
    with pytest.raises(ApiError) as error:
        await prepare_report_photos([UploadFile(BytesIO(b'')) for _ in range(6)])
    assert error.value.code == 'REPORT_PHOTOS_LIMIT'
