from io import BytesIO

import pytest
from PIL import Image

from app.core.errors import AppError
from app.media.images import decode_jpeg, sanitize_jpeg


def jpeg(size=(40, 20), *, metadata=False):
    with Image.new("RGB", size, "red") as image, BytesIO() as output:
        image.paste("blue", (size[0] // 2, 0, size[0], size[1]))
        exif = Image.Exif()
        if metadata:
            exif[274] = 2  # Must not mirror the already mirrored FE frame again.
            exif[270] = "private-camera-metadata"
        image.save(output, format="JPEG", quality=95, exif=exif, icc_profile=b"private-profile")
        return output.getvalue()


def test_result_is_entire_frame_without_metadata_or_another_flip():
    source = jpeg(metadata=True)
    result = sanitize_jpeg(source)
    assert b"private-camera-metadata" not in result
    assert b"private-profile" not in result
    with Image.open(BytesIO(result)) as image:
        assert image.size == (40, 20)
        assert not image.getexif()
        assert "icc_profile" not in image.info
        left, right = image.getpixel((5, 10)), image.getpixel((35, 10))
        assert left[0] > left[2] + 150
        assert right[2] > right[0] + 150


@pytest.mark.parametrize("data", [b"", b"not a jpeg", b"\xff\xd8not a jpeg"])
def test_invalid_jpeg_rejected(data):
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        sanitize_jpeg(data)


def test_png_and_truncated_jpeg_rejected():
    with Image.new("RGB", (40, 20)) as image, BytesIO() as output:
        image.save(output, format="PNG")
        with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
            sanitize_jpeg(output.getvalue())
    data = jpeg()
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        sanitize_jpeg(data[: len(data) // 2])


def test_byte_and_pixel_limits_before_full_decode():
    data = jpeg()
    with pytest.raises(AppError, match="PAYLOAD_TOO_LARGE"):
        sanitize_jpeg(data, max_bytes=len(data) - 1)
    with pytest.raises(AppError, match="PAYLOAD_TOO_LARGE"):
        sanitize_jpeg(data, max_pixels=799)
    assert sanitize_jpeg(data, max_bytes=len(data), max_pixels=800)


def test_decoded_image_closes_on_success_and_exception():
    with decode_jpeg(jpeg()) as image:
        assert image.mode == "RGB"
    with pytest.raises(ValueError, match="closed"):
        image.getpixel((0, 0))
    with pytest.raises(RuntimeError, match="consumer"):
        with decode_jpeg(jpeg()) as second:
            raise RuntimeError("consumer")
    with pytest.raises(ValueError, match="closed"):
        second.getpixel((0, 0))


def test_image_processing_does_not_use_temporary_files(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No image temporary files")

    monkeypatch.setattr("tempfile.TemporaryFile", forbidden)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", forbidden)
    assert sanitize_jpeg(jpeg())
