import time
from datetime import UTC, datetime, timedelta

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def from_epoch_ms(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def now_utc() -> datetime:
    return from_epoch_ms(now_ms())
