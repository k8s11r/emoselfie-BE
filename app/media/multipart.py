"""Minimal in-memory multipart reader. §8.3 forbids spooling an upload to disk (PV-05)."""

from fastapi import Request

from app.core.errors import AppError

MAX_PARTS = 8
MAX_HEADER_BYTES = 8192


async def read_capped(request: Request, limit: int) -> bytes:
    """Read a request body in memory while enforcing a streaming byte limit."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise AppError("PAYLOAD_TOO_LARGE")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise AppError("PAYLOAD_TOO_LARGE")
    return bytes(body)


def boundary_of(content_type: str | None) -> bytes:
    if content_type is None or not content_type.lower().startswith("multipart/form-data"):
        raise AppError("UNSUPPORTED_MEDIA")
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.strip().partition("=")
        if name.strip().lower() != "boundary":
            continue
        value = value.strip().strip('"')
        if not 1 <= len(value) <= 70 or not value.isascii():
            raise AppError("UNSUPPORTED_MEDIA")
        return value.encode("ascii")
    raise AppError("UNSUPPORTED_MEDIA")


def _disposition_name(headers: bytes) -> str | None:
    for line in headers.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() != b"content-disposition":
            continue
        for parameter in value.decode("latin-1").split(";")[1:]:
            key, _, raw = parameter.strip().partition("=")
            if key.strip().lower() == "name":
                return raw.strip().strip('"')
    return None


def read_part(body: bytes, boundary: bytes, field: str) -> bytes:
    """Return one field's bytes. The boundary cannot occur inside a part by definition."""
    delimiter = b"--" + boundary
    closing = b"\r\n" + delimiter + b"--"
    # A truncated upload has no closing delimiter; accepting it would score half a photo.
    if not body.startswith(delimiter) or closing not in body:
        raise AppError("UNSUPPORTED_MEDIA")
    segments = body.split(b"\r\n" + delimiter)
    segments[0] = segments[0][len(delimiter) :]
    if len(segments) > MAX_PARTS:
        raise AppError("UNSUPPORTED_MEDIA")
    for segment in segments:
        if segment.startswith(b"--"):
            break  # The closing delimiter ends the body; trailing epilogue is ignored.
        if not segment.startswith(b"\r\n"):
            raise AppError("UNSUPPORTED_MEDIA")
        head, separator, payload = segment[2:].partition(b"\r\n\r\n")
        if not separator or len(head) > MAX_HEADER_BYTES:
            raise AppError("UNSUPPORTED_MEDIA")
        if _disposition_name(head) == field:
            return payload
    raise AppError("UNSUPPORTED_MEDIA")
