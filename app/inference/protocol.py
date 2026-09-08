import math
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import EmotionLabel

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class EmotionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    face_detected: bool
    probabilities: dict[EmotionLabel, Probability] | None = None
    face_box: tuple[int, int, int, int] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if not self.face_detected:
            if self.probabilities is not None or self.face_box is not None:
                raise ValueError("No-face results cannot contain probabilities or a face box")
        elif (
            self.probabilities is None
            or set(self.probabilities) != set(EmotionLabel)
            or not math.isclose(sum(self.probabilities.values()), 1.0, abs_tol=1e-6)
        ):
            raise ValueError("Detected faces require all seven probabilities summing to one")
        return self


class InferenceError(Exception):
    """Engine failure, distinct from a successful detection with no face."""


class EmotionClassifier(Protocol):
    async def classify(self, image: bytes) -> EmotionResult: ...


class ManagedEmotionClassifier(EmotionClassifier, Protocol):
    async def aclose(self) -> None: ...
