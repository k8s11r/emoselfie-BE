import base64
import hashlib
import hmac
from uuid import UUID

from app.core.config import Settings

COOKIE_NAME = "es_uid"
COOKIE_MAX_AGE = 31536000


def sign_cookie(user_id: UUID, secret: str) -> str:
    signature = hmac.digest(secret.encode(), str(user_id).encode(), "sha256")[:16]
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{user_id}.{encoded}"


def verify_cookie(value: str | None, settings: Settings) -> tuple[UUID, bool] | None:
    if value is None or len(value) != 59 or not value.isascii():
        return None
    try:
        raw_id, _ = value.split(".", 1)
        user_id = UUID(raw_id)
    except (ValueError, AttributeError):
        return None
    if user_id.version != 4 or str(user_id) != raw_id:
        return None
    secrets = [settings.cookie_secret]
    if settings.cookie_secret_previous is not None:
        secrets.append(settings.cookie_secret_previous)
    for index, secret in enumerate(secrets):
        if hmac.compare_digest(sign_cookie(user_id, secret.get_secret_value()), value):
            return user_id, index > 0
    return None


def private_rate_subject(subject: str) -> str:
    # Internal Redis keys need a stable opaque subject, never a raw IP or UUID.
    return hashlib.sha256(subject.encode()).hexdigest()


def capture_token(round_id: int, participant_id: int, deadline_at_ms: int, secret: str) -> str:
    """CP-03 §6.3. Proves a capture session was issued, never that the pixels came from it."""
    payload = f"{round_id}:{participant_id}:{deadline_at_ms}"
    digest = hmac.digest(secret.encode(), payload.encode(), "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")[:32]


def verify_capture_token(
    token: str | None, round_id: int, participant_id: int, deadline_at_ms: int, secret: str
) -> bool:
    if token is None or len(token) != 32 or not token.isascii():
        return False
    return hmac.compare_digest(
        capture_token(round_id, participant_id, deadline_at_ms, secret), token
    )


def media_token(
    round_id: int, submission_id: int, viewer_participant_id: int, expires_at_ms: int, secret: str
) -> str:
    """§8.4. Personal per viewer, so a leaked link opens for nobody else."""
    payload = f"{round_id}:{submission_id}:{viewer_participant_id}:{expires_at_ms}"
    signature = hmac.digest(secret.encode(), payload.encode(), "sha256")
    return (
        base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")[:32]
    )


def verify_media_token(token: str, secret: str) -> tuple[int, int, int, int] | None:
    """Returns roundId, submissionId, viewerParticipantId and expiry, or None when untrusted."""
    if len(token) > 256 or not token.isascii() or token.count(".") != 1:
        return None
    encoded, signature = token.split(".", 1)
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
        round_id, submission_id, viewer_id, expires_at_ms = (
            int(part) for part in payload.split(":")
        )
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.digest(secret.encode(), payload.encode(), "sha256")
    encoded_signature = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")[:32]
    if not hmac.compare_digest(encoded_signature, signature):
        return None
    return round_id, submission_id, viewer_id, expires_at_ms
