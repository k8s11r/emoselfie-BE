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
