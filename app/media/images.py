from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.core.errors import AppError


@contextmanager
def decode_jpeg(
    data: bytes, *, max_bytes: int = 2097152, max_pixels: int = 4194304
) -> Iterator[Image.Image]:
    """Decode into owned RGB memory. No EXIF rotation, filesystem or global Pillow settings."""
    if len(data) > max_bytes:
        raise AppError("PAYLOAD_TOO_LARGE")
    if not data or not data.startswith(b"\xff\xd8"):
        raise AppError("UNSUPPORTED_MEDIA")
    try:
        with BytesIO(data) as stream, Image.open(stream, formats=["JPEG"]) as source:
            if source.width * source.height > max_pixels:
                raise AppError("PAYLOAD_TOO_LARGE")
            if getattr(source, "n_frames", 1) != 1:
                raise AppError("UNSUPPORTED_MEDIA")
            source.load()
            rgb = source.convert("RGB")
    except Image.DecompressionBombError:
        raise AppError("PAYLOAD_TOO_LARGE") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise AppError("UNSUPPORTED_MEDIA") from None
    try:
        yield rgb
    finally:
        rgb.close()


def encode_result_jpeg(image: Image.Image) -> bytes:
    """Preserve the entire frame and discard EXIF/ICC/comments and other input metadata."""
    with Image.new("RGB", image.size) as clean, BytesIO() as output:
        clean.paste(image)
        clean.save(output, format="JPEG", quality=80)
        return output.getvalue()


def sanitize_jpeg(data: bytes, *, max_bytes: int = 2097152, max_pixels: int = 4194304) -> bytes:
    with decode_jpeg(data, max_bytes=max_bytes, max_pixels=max_pixels) as image:
        return encode_result_jpeg(image)


def inspect_jpeg(
    data: bytes, *, max_bytes: int = 2097152, max_pixels: int = 4194304
) -> tuple[int, int]:
    """Header-only check for the accept path. The full decode still happens in the engine."""
    if len(data) > max_bytes:
        raise AppError("PAYLOAD_TOO_LARGE")
    if not data.startswith(b"\xff\xd8"):
        raise AppError("UNSUPPORTED_MEDIA")
    try:
        with BytesIO(data) as stream, Image.open(stream, formats=["JPEG"]) as source:
            size = source.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise AppError("UNSUPPORTED_MEDIA") from None
    if size[0] * size[1] > max_pixels:
        raise AppError("PAYLOAD_TOO_LARGE")
    return size
