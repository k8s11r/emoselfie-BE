from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core import clock
from app.core.config import Settings
from app.core.schemas import PublicId, WireModel


def test_clock_uses_one_boundary(fixed_clock):
    assert clock.to_epoch_ms(clock.now_utc()) == fixed_clock
    assert clock.now_utc().tzinfo == UTC


def test_epoch_conversion_handles_offsets_and_truncates():
    instant = datetime(1970, 1, 1, 9, 0, 0, 1999, tzinfo=timezone(timedelta(hours=9)))
    assert clock.to_epoch_ms(instant) == 1
    assert clock.to_epoch_ms(clock.from_epoch_ms(-1)) == -1
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.to_epoch_ms(datetime(2026, 9, 8))


@pytest.mark.parametrize(
    "change",
    [
        {"app_env": "production"},
        {"cookie_secret": "short"},
        {"capture_token_secret": "c" * 48},
        {"cookie_secret_previous": "short"},
        {"redis_media_url": "redis://127.0.0.1:56379/1"},
        {"database_url": "postgresql://localhost/db"},
        {"inference_concurrency": 0},
        {"inference_timeout_sec": float("nan")},
        {"scheduler_tick_ms": 0},
    ],
)
def test_invalid_configuration_rejected(settings, change):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), **change})


def test_config_does_not_repr_secrets(settings):
    assert settings.cookie_secret.get_secret_value() not in repr(settings)
    assert "local-development" not in repr(settings)
    assert settings.cookie_secret_previous is None


def test_wire_ids_camelcase_and_null():
    class Result(WireModel):
        participant_id: PublicId
        next_round_at_ms: int | None = None

    assert Result(participant_id=9007199254740993).model_dump(mode="json", by_alias=True) == {
        "participantId": "9007199254740993",
        "nextRoundAtMs": None,
    }
