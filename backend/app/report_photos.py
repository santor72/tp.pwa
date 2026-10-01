"""Read user photographs by their contents and prepare a standard JPEG."""
import asyncio
from io import BytesIO
from pathlib import PurePosixPath
import struct
from threading import BoundedSemaphore

from fastapi import UploadFile
import pillow_avif  # noqa: F401 -- registers the AVIF decoder with Pillow
from pillow_heif import register_heif_opener
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from app.errors import ApiError

MAX_INTAKE_BYTES = 25 * 1024 * 1024
MAX_REPORT_PHOTO_BYTES = 50 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # Output accepted by GIS.
MAX_IMAGE_PIXELS = 120_000_000
JPEG_LONG_SIDE = 4096
JPEG_QUALITY = 85
SUPPORTED_FORMATS = ('JPEG', 'PNG', 'WEBP', 'HEIF', 'AVIF', 'TIFF', 'BMP', 'GIF')

register_heif_opener(thumbnails=False)
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
# LittleCMS timestamps newly created profiles. Fix the timestamp so retrying the
# same source on another worker produces the same JPEG and GIS fingerprint.
_srgb_bytes = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
_srgb_bytes[24:36] = struct.pack('>6H', 2000, 1, 1, 0, 0, 0)
_SRGB = ImageCms.ImageCmsProfile(BytesIO(_srgb_bytes))
# A large source can occupy hundreds of MB. Only one decoder runs per process;
# routes offload this work to a thread, keeping the event loop available.
_DECODE_SLOT = BoundedSemaphore(1)


def _is_camera_raw(content: bytes) -> bool:
    if content.startswith((b'FUJIFILMCCD-RAW', b'IIRO', b'MMOR', b'IIU\x00')):
        return True
    if content[:4] not in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'):
        return False
    if content[8:12] == b'CR\x02\x00':
        return True
    # DNG, NEF and ARW are TIFF containers, sometimes with an ordinary preview
    # in the first directory. Inspect nested image directories before decoding.
    endian = '<' if content[:2] == b'II' else '>'
    big = content[2:4] == (b'+\x00' if endian == '<' else b'\x00+')
    def number(fmt: str, offset: int) -> int:
        if offset < 0 or offset + struct.calcsize(fmt) > len(content):
            raise ValueError('invalid TIFF directory offset')
        return struct.unpack_from(endian + fmt, content, offset)[0]
    try:
        pending = [number('Q' if big else 'I', 8 if big else 4)]
        seen: set[int] = set()
        examined = 0
        while pending:
            offset = pending.pop()
            if not offset or offset in seen:
                continue
            seen.add(offset)
            if len(seen) > 256:
                raise ValueError('too many TIFF directories')
            count = number('Q' if big else 'H', offset)
            examined += count
            if examined > 4096:
                raise ValueError('too many TIFF directory entries')
            entry_size, count_size, inline_size = (20, 8, 8) if big else (12, 2, 4)
            for index in range(count):
                entry = offset + count_size + index * entry_size
                tag, kind = number('H', entry), number('H', entry + 2)
                if tag in {33421, 33422, 50706, 50707}:  # CFA and DNG tags.
                    return True
                size = number('Q' if big else 'I', entry + 4)
                value_at = entry + (12 if big else 8)
                if tag == 262 and number('H', value_at) in {32803, 34892}:
                    return True  # CFA / LinearRaw photometric interpretation.
                if tag == 330 and kind in {3, 4, 13, 16, 18}:  # SubIFDs.
                    fmt, width = ('H', 2) if kind == 3 else (('Q', 8) if kind in {16, 18} else ('I', 4))
                    if size > 4096:
                        raise ValueError('too many TIFF subdirectories')
                    if size * width > inline_size:
                        value_at = number('Q' if big else 'I', value_at)
                    pending.extend(number(fmt, value_at + n * width) for n in range(size))
            pending.append(number('Q' if big else 'I', offset + count_size + count * entry_size))
        return False
    except struct.error as exc:
        raise ValueError('invalid TIFF directory') from exc


def _reject_non_photo(content: bytes, code_prefix: str, label: str) -> None:
    if _is_camera_raw(content):
        reason = 'RAW-файлы камеры не поддерживаются'
    elif content.lstrip().startswith(b'%PDF-'):
        reason = 'PDF не поддерживается — выберите фотографию'
    elif content.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<') and b'<svg' in content[:4096].lower():
        reason = 'SVG не поддерживается — выберите фотографию'
    elif (content[:4] == b'RIFF' and content[8:12] == b'AVI ') or content.startswith((b'\x1aE\xdf\xa3', b'OggS', b'\x00\x00\x01\xba')):
        reason = 'Видео не поддерживается — выберите фотографию'
    elif len(content) >= 12 and content[4:8] == b'ftyp':
        box_size = int.from_bytes(content[:4], 'big')
        brands = {content[8:12], *(content[n:n + 4] for n in range(16, min(box_size, len(content), 4096), 4))}
        if brands & {b'avif', b'avis', b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1'}:
            return
        reason = 'RAW-файлы камеры не поддерживаются' if b'crx ' in brands else 'Видео не поддерживается — выберите фотографию'
    else:
        return
    raise ApiError(422, f'{code_prefix}_PHOTO_TYPE', f'{label}: {reason}')


def _to_srgb(image: Image.Image) -> Image.Image:
    profile = image.info.get('icc_profile')
    alpha = image.convert('RGBA').getchannel('A') if image.mode in {'RGBA', 'LA', 'PA'} or 'transparency' in image.info else None
    if image.mode.startswith('I;16'):
        color = image.convert('I').point(lambda value: value / 257).convert('L')
    elif image.mode in {'RGB', 'L', 'CMYK', 'LAB'}:
        color = image
    else:
        color = image.convert('RGB')
    if profile:
        try:
            color = ImageCms.profileToProfile(color, ImageCms.ImageCmsProfile(BytesIO(profile)), _SRGB, outputMode='RGB')
        except (OSError, ValueError) as exc:
            raise ImageCms.PyCMSError('invalid embedded profile') from exc
    else:
        color = color.convert('RGB')
    if alpha is not None:
        background = Image.new('RGB', color.size, 'white')
        background.paste(color, mask=alpha)
        return background
    return color


def normalize_report_photo(content: bytes, *, filename: str = 'photo', code_prefix: str = 'REPORT', label: str = 'Фото') -> dict:
    if len(content) > MAX_INTAKE_BYTES:
        raise ApiError(422, f'{code_prefix}_PHOTO_SIZE', f'{label}: размер исходного файла — не более 25 МиБ')
    with _DECODE_SLOT:
        try:
            _reject_non_photo(content, code_prefix, label)
            with Image.open(BytesIO(content), formats=SUPPORTED_FORMATS) as image:
                # HEIF opens its primary image; other formats open their first frame.
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise Image.DecompressionBombError('more than 120 megapixels')
                image.load()  # Validate the selected image, including its pixel data.
                ImageOps.exif_transpose(image, in_place=True)
                image.thumbnail((JPEG_LONG_SIDE, JPEG_LONG_SIDE), Image.Resampling.LANCZOS)
                rgb = _to_srgb(image)
                output = BytesIO()
                while True:
                    output.seek(0)
                    output.truncate()
                    rgb.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True, icc_profile=_SRGB.tobytes())
                    prepared = output.getvalue()
                    if len(prepared) <= MAX_PHOTO_BYTES:
                        break
                    # Highly detailed images can still exceed GIS's 10 MiB.
                    # Keep quality 85 and reduce the dimensions further.
                    scale = (MAX_PHOTO_BYTES / len(prepared)) ** 0.5 * 0.9
                    rgb = rgb.resize((max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))), Image.Resampling.LANCZOS)
        except Image.DecompressionBombError as exc:
            raise ApiError(422, f'{code_prefix}_PHOTO_DIMENSIONS', f'{label}: разрешение исходного изображения — не более 120 мегапикселей') from exc
        except ImageCms.PyCMSError as exc:
            raise ApiError(422, f'{code_prefix}_PHOTO_COLOR_PROFILE', f'{label}: не удалось прочитать цветовой профиль фотографии') from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError, RuntimeError) as exc:
            raise ApiError(422, f'{code_prefix}_PHOTO_CONTENT', f'{label}: файл повреждён или формат изображения не поддерживается') from exc
    name = PurePosixPath(filename.replace('\\', '/')).name
    stem = PurePosixPath(name).stem[:200] or 'photo'
    return {'name': f'{stem}.jpg', 'content_type': 'image/jpeg', 'content': prepared}


async def prepare_report_photos(photos: list[UploadFile], *, code_prefix: str = 'REPORT') -> list[dict]:
    if len(photos) > 5:
        raise ApiError(422, f'{code_prefix}_PHOTOS_LIMIT', 'В одном отчёте можно загрузить до 5 фотографий')
    prepared = []
    total = 0
    for index, photo in enumerate(photos, 1):
        content = await photo.read(MAX_INTAKE_BYTES + 1)
        total += len(content)
        if total > MAX_REPORT_PHOTO_BYTES:
            raise ApiError(422, f'{code_prefix}_SIZE_LIMIT', 'Общий размер исходных фотографий — не более 50 МиБ')
        prepared.append(await asyncio.to_thread(
            normalize_report_photo, content, filename=photo.filename or 'photo',
            code_prefix=code_prefix, label=f'Фото {index}',
        ))
    return prepared
