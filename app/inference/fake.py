import asyncio
from typing import Literal

from app.domain.enums import EmotionLabel
from app.inference.protocol import EmotionResult, InferenceError


class FakeClassifier:
    """Deterministic test engine. Never keeps input images or falls back from a real model."""

    def __init__(
        self,
        scenario: Literal["success", "no_face", "failed", "timeout"] = "success",
        result: EmotionResult | None = None,
    ) -> None:
        self.scenario = scenario
        self.result = result or EmotionResult(
            face_detected=True,
            probabilities={
                label: 1.0 if label == EmotionLabel.HAPPY else 0.0 for label in EmotionLabel
            },
        )

    async def classify(self, image: bytes) -> EmotionResult:
        if self.scenario == "timeout":
            await asyncio.Event().wait()
        return self.classify_sync(image)

    def classify_sync(self, image: bytes) -> EmotionResult:
        if self.scenario == "timeout":
            raise ValueError("Use a controlled blocking test backend for native timeout tests")
        if self.scenario == "failed":
            raise InferenceError("Fake inference failure")
        if self.scenario == "no_face":
            return EmotionResult(face_detected=False)
        return self.result.model_copy(deep=True)

    async def aclose(self) -> None:
        pass
