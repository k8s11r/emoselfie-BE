from pathlib import Path
from typing import Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"] = "production"
    inference_backend: Literal["fake", "real"] = "real"
    database_url: PostgresDsn = Field(repr=False)
    redis_url: RedisDsn = Field(repr=False)
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
        if (self.redis_url.host, self.redis_url.port) == (
            self.redis_media_url.host,
            self.redis_media_url.port,
        ):
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
