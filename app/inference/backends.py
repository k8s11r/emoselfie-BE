import threading
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np
import torch
from numpy.typing import NDArray
from PIL import Image

from app.inference.pipeline import FaceBox
from app.inference.protocol import InferenceError
from app.inference.vendor.ryumina import ResNet50


class TorchEmotionModel:
    def __init__(self, path: Path, *, threads: int = 1) -> None:
        torch.set_num_threads(threads)
        model = cast(torch.nn.Module, ResNet50(7, channels=3))
        state = torch.load(path, map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
        model.eval()
        model.requires_grad_(False)
        self._model: torch.nn.Module | None = model

    def predict_logits(self, batch: NDArray[np.float32]) -> Sequence[float]:
        if self._model is None:
            raise InferenceError("Emotion model is closed")
        with torch.inference_mode():
            output = self._model(torch.from_numpy(batch))
        if output.shape != (1, 7):
            raise InferenceError("Unexpected emotion model output shape")
        return tuple(float(value) for value in output[0].tolist())

    def close(self) -> None:
        self._model = None


class MediaPipeFaceDetector:
    def __init__(self, path: Path) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.FaceDetectorOptions(
            base_options=python.BaseOptions(
                model_asset_path=str(path), delegate=python.BaseOptions.Delegate.CPU
            ),
            running_mode=vision.RunningMode.IMAGE,
        )
        self._detector = vision.FaceDetector.create_from_options(options)
        self._lock = threading.Lock()
        self._closed = False

    def detect(self, image: Image.Image) -> Sequence[FaceBox]:
        with self._lock:
            if self._closed:
                raise InferenceError("Face detector is closed")
            frame = self._mp.Image(
                image_format=self._mp.ImageFormat.SRGB,
                data=np.ascontiguousarray(image, dtype=np.uint8),
            )
            result = self._detector.detect(frame)
            return [
                FaceBox(
                    item.bounding_box.origin_x,
                    item.bounding_box.origin_y,
                    item.bounding_box.width,
                    item.bounding_box.height,
                )
                for item in result.detections
            ]

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._detector.close()
