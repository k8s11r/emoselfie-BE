from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import AfterValidator, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_REDIS_PORT = 6379
REDIS_SCHEMES = {"redis", "rediss", "redis+sentinel"}


def _endpoints(url: str) -> set[tuple[str, int]]:
    """URL이 가리키는 (host, port) 집합. Sentinel URL은 여러 개를 나열한다."""
    netloc = urlparse(url).netloc.rsplit("@", 1)[-1]
    resolved = set()
    for entry in netloc.split(","):
        host, _, port = entry.rpartition(":")
        if not host:  # 포트가 없으면 rpartition이 host를 비운다
            host, port = entry, ""
        resolved.add((host, int(port) if port else DEFAULT_REDIS_PORT))
    return resolved


def _check_redis_url(value: str) -> str:
    # RedisDsn은 redis/rediss만 허용하고 쉼표로 나열된 다중 호스트를 파싱하지
    # 못한다. Sentinel은 최소 3대가 필요해 그 형식을 피할 수 없으므로 직접 검증한다.
    #   redis+sentinel://host1:26379,host2:26379,host3:26379/0/mymaster
    scheme = urlparse(value).scheme
    if scheme not in REDIS_SCHEMES:
        raise ValueError(f"REDIS_URL must use one of {sorted(REDIS_SCHEMES)}")
    if not _endpoints(value):
        raise ValueError("REDIS_URL must name at least one host")
    return value


RedisUrl = Annotated[str, AfterValidator(_check_redis_url)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"] = "production"
    inference_backend: Literal["fake", "real"] = "real"
    database_url: PostgresDsn = Field(repr=False)
    redis_url: RedisUrl = Field(repr=False)
    redis_media_url: RedisDsn = Field(repr=False)
    cookie_secret: SecretStr
    cookie_secret_previous: SecretStr | None = None
    capture_token_secret: SecretStr
    media_token_secret: SecretStr
    emotion_model_version: str = Field(default="v1", min_length=1)
    emotion_model_path: Path = Path("/models/emotion/v1/FER_static_ResNet50_AffectNet.pt")
    face_model_version: str = Field(default="v1", min_length=1)
    face_model_path: Path = Path("/models/face/v1/blaze_face_short_range.tflite")
    inference_concurrency: int = Field(default=2, ge=1)
    inference_timeout_sec: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    inference_queue_capacity: int = Field(default=24, ge=1)
    inference_torch_threads: int = Field(default=1, ge=1)
    max_upload_bytes: int = Field(default=2097152, gt=0)
    max_image_pixels: int = Field(default=4194304, gt=0)
    host_grace_sec: int = Field(default=60, gt=0)
    participant_grace_sec: int = Field(default=60, gt=0)
    room_idle_expire_sec: int = Field(default=1800, gt=0)
    scheduler_tick_ms: int = Field(default=250, gt=0)
    dependency_timeout_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_runtime(self) -> Self:
        if self.app_env == "production" and self.inference_backend == "fake":
            raise ValueError("Fake inference is restricted to development and test")
        if self.database_url.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        if _endpoints(self.redis_url) & _endpoints(str(self.redis_media_url)):
            raise ValueError("Coordination and image Redis must use separate instances")
        active = [self.cookie_secret, self.capture_token_secret, self.media_token_secret]
        if any(len(secret.get_secret_value()) < 32 for secret in active):
            raise ValueError("Signing secrets must contain at least 32 characters")
        if len({secret.get_secret_value() for secret in active}) != 3:
            raise ValueError("Each signing purpose requires an independent secret")
        if self.cookie_secret_previous is not None:
            previous = self.cookie_secret_previous.get_secret_value()
            if not previous:
                self.cookie_secret_previous = None
            elif len(previous) < 32:
                raise ValueError("Previous cookie secret must contain at least 32 characters")
        return self
