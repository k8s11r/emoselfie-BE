import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.enums import EmotionLabel
from app.inference.protocol import EmotionResult, InferenceError
from app.media.images import decode_jpeg


@dataclass(frozen=True, repr=False)
class FaceBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Face coordinates must be integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Face dimensions must be positive")


class FaceDetector(Protocol):
    def detect(self, image: Image.Image) -> Sequence[FaceBox]: ...
    def close(self) -> None: ...


class EmotionModel(Protocol):
    def predict_logits(self, batch: NDArray[np.float32]) -> Sequence[float]: ...
    def close(self) -> None: ...


class PreprocessingSpec(BaseModel):
    """Model-version contract. Exact mean and interpolation must come from femo.py."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    size: Literal[224] = 224
    bgr_mean: tuple[float, float, float]
    resampling: Literal["nearest", "bilinear", "bicubic", "lanczos"]

    @model_validator(mode="after")
    def finite_mean(self) -> Self:
        if not all(math.isfinite(value) for value in self.bgr_mean):
            raise ValueError("BGR mean must be finite")
        return self


def largest_face(boxes: Sequence[FaceBox], image_size: tuple[int, int]) -> FaceBox | None:
    """Use the largest visible area; clip detector boxes before PIL can pad a crop."""
    width, height = image_size
    best: FaceBox | None = None
    for box in boxes:
        left, top = max(0, box.x), max(0, box.y)
        right, bottom = min(width, box.x + box.width), min(height, box.y + box.height)
        if right <= left or bottom <= top:
            continue
        candidate = FaceBox(left, top, right - left, bottom - top)
        if best is None or candidate.width * candidate.height > best.width * best.height:
            best = candidate
    return best


def preprocess(face: Image.Image, spec: PreprocessingSpec) -> NDArray[np.float32]:
    resampling = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }[spec.resampling]
    with face.resize((spec.size, spec.size), resample=resampling) as resized:
        rgb = np.asarray(resized, dtype=np.float32)
        bgr = rgb[:, :, ::-1] - np.asarray(spec.bgr_mean, dtype=np.float32)
        # Raw 0..255 BGR minus the model mean. Never /255 or ImageNet normalization.
        return np.ascontiguousarray(bgr.transpose(2, 0, 1)[np.newaxis, ...])


class EmotionPipeline:
    """Synchronous inference boundary; the runner owns threading and resource lifetime."""

    def __init__(
        self,
        detector: FaceDetector,
        model: EmotionModel,
        *,
        labels: tuple[EmotionLabel, ...],
        preprocessing: PreprocessingSpec,
        max_bytes: int = 2097152,
        max_pixels: int = 4194304,
    ) -> None:
        if len(labels) != 7 or set(labels) != set(EmotionLabel):
            raise ValueError("Model labels must contain each of the seven emotions exactly once")
        self.detector = detector
        self.model = model
        self.labels = labels
        self.preprocessing = preprocessing
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self._closed = False

    def _probabilities(self, face: Image.Image) -> dict[EmotionLabel, float]:
        batch = preprocess(face, self.preprocessing)
        try:
            logits = np.asarray(self.model.predict_logits(batch), dtype=np.float64)
        finally:
            del batch
        if logits.shape != (7,) or not np.isfinite(logits).all():
            raise InferenceError("Model output must be seven finite logits")
        weights = np.exp(logits - logits.max())
        probabilities = weights / weights.sum()
        return {
            label: float(value) for label, value in zip(self.labels, probabilities, strict=True)
        }

    def classify(self, data: bytes) -> EmotionResult:
        if self._closed:
            raise InferenceError("Inference pipeline is closed")
        with decode_jpeg(data, max_bytes=self.max_bytes, max_pixels=self.max_pixels) as image:
            box = largest_face(self.detector.detect(image), image.size)
            if box is None:
                return EmotionResult(face_detected=False)
            with image.crop((box.x, box.y, box.x + box.width, box.y + box.height)) as face:
                probabilities = self._probabilities(face)
            return EmotionResult(
                face_detected=True,
                probabilities=probabilities,
                face_box=(box.x, box.y, box.width, box.height),
            )

    def warmup(self) -> None:
        if self._closed:
            raise InferenceError("Inference pipeline is closed")
        with Image.new("RGB", (224, 224)) as image:
            self.detector.detect(image)
            # A synthetic warmup may contain no face; still exercise the emotion model.
            self._probabilities(image)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.detector.close()
            finally:
                self.model.close()
