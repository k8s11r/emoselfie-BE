import pytest

from app.core.errors import AppError
from app.media.multipart import boundary_of, read_part

BOUNDARY = "emoselfie-boundary"


def body(*parts, boundary=BOUNDARY, closed=True):
    chunks = []
    for name, payload in parts:
        chunks.append(
            f"--{boundary}\r\n".encode()
            + f'Content-Disposition: form-data; name="{name}"; filename="c.jpg"\r\n'.encode()
            + b"Content-Type: image/jpeg\r\n\r\n"
            + payload
            + b"\r\n"
        )
    return b"".join(chunks) + (f"--{boundary}--\r\n".encode() if closed else b"")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"multipart/form-data; boundary={BOUNDARY}", BOUNDARY.encode()),
        (f'multipart/form-data; boundary="{BOUNDARY}"', BOUNDARY.encode()),
        (f"Multipart/Form-Data; charset=utf-8; BOUNDARY={BOUNDARY}", BOUNDARY.encode()),
    ],
)
def test_boundary_is_read_from_the_content_type(header, expected):
    assert boundary_of(header) == expected


@pytest.mark.parametrize(
    "header",
    [None, "application/json", "multipart/form-data", "multipart/form-data; boundary=", "text/*"],
)
def test_requests_without_a_usable_boundary_are_unsupported(header):
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        boundary_of(header)


def test_binary_payload_survives_intact():
    image = bytes(range(256)) * 8
    parsed = read_part(body(("image", image)), BOUNDARY.encode(), "image")

    assert parsed == image


def test_the_named_field_is_selected_and_others_are_ignored():
    parsed = read_part(
        body(("clientSubmittedAt", b"1757203355120"), ("image", b"\xff\xd8jpeg")),
        BOUNDARY.encode(),
        "image",
    )

    assert parsed == b"\xff\xd8jpeg"


def test_a_missing_field_is_unsupported_media():
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        read_part(body(("other", b"data")), BOUNDARY.encode(), "image")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not multipart at all",
        b"--other-boundary\r\n\r\ndata\r\n--other-boundary--\r\n",
    ],
)
def test_malformed_bodies_are_rejected(payload):
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        read_part(payload, BOUNDARY.encode(), "image")


def test_a_part_without_a_header_separator_is_rejected():
    broken = f"--{BOUNDARY}\r\nContent-Disposition: form-data\r\n".encode()
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        read_part(broken, BOUNDARY.encode(), "image")


def test_too_many_parts_are_rejected():
    parts = [(f"field{index}", b"x") for index in range(12)]
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        read_part(body(*parts), BOUNDARY.encode(), "image")


def test_a_truncated_body_is_rejected_instead_of_scoring_half_a_photo():
    with pytest.raises(AppError, match="UNSUPPORTED_MEDIA"):
        read_part(body(("image", b"\xff\xd8data"), closed=False), BOUNDARY.encode(), "image")
