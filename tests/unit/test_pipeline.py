from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from app.domain.enums import EmotionLabel
from app.inference.loader import MODEL_LABELS, MODEL_PREPROCESSING
from app.inference.pipeline import EmotionPipeline, FaceBox, largest_face, preprocess
from app.inference.protocol import InferenceError


class Detector:
    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0
        self.closed = False

    def detect(self, image):
        self.calls += 1
        return self.boxes

    def close(self):
        self.closed = True


class Model:
    def __init__(self, logits=None):
        self.logits = [0, 1000, 0, 0, 0, 0, 0] if logits is None else logits
        self.calls = 0
        self.closed = False

    def predict_logits(self, batch):
        self.calls += 1
        assert batch.dtype == np.float32
        assert batch.shape == (1, 3, 224, 224)
        return self.logits

    def close(self):
        self.closed = True


def sample():
    with Image.new("RGB", (80, 40), "red") as image, BytesIO() as output:
        image.save(output, format="JPEG")
        return output.getvalue()


def pipeline(boxes, logits=None):
    detector, model = Detector(boxes), Model(logits)
    return EmotionPipeline(detector, model, labels=MODEL_LABELS, preprocessing=MODEL_PREPROCESSING)


def test_preprocessing_is_nearest_raw_bgr_with_exact_means():
    with Image.new("RGB", (2, 1)) as image:
        image.putpixel((0, 0), (10, 20, 30))
        image.putpixel((1, 0), (210, 220, 230))
        batch = preprocess(image, MODEL_PREPROCESSING)
    expected_left = np.asarray([30, 20, 10], dtype=np.float32) - np.asarray(
        [91.4953, 103.8827, 131.0912], dtype=np.float32
    )
    np.testing.assert_array_equal(batch[0, :, 0, 0], expected_left)
    np.testing.assert_array_equal(batch[0, :, 223, 111], expected_left)
    np.testing.assert_array_equal(batch[0, :, 0, 112], expected_left + 200)
    assert batch.flags.c_contiguous


def test_largest_visible_face_and_out_of_bounds_boxes():
    small = FaceBox(0, 0, 10, 10)
    large = FaceBox(20, 5, 50, 30)
    assert largest_face([small, large], (80, 40)) == large
    assert largest_face([FaceBox(-5, -5, 15, 15)], (80, 40)) == small
    assert largest_face([FaceBox(90, 0, 5, 5)], (80, 40)) is None


def test_no_face_never_calls_emotion_model():
    instance = pipeline([])
    try:
        result = instance.classify(sample())
        assert result.face_detected is False
        assert result.probabilities is None
        assert instance.model.calls == 0
    finally:
        instance.close()


def test_label_order_and_largest_crop_map_to_happy():
    instance = pipeline([FaceBox(0, 0, 10, 10), FaceBox(10, 0, 50, 40)])
    try:
        result = instance.classify(sample())
        assert result.probabilities[EmotionLabel.HAPPY] == 1
        assert result.probabilities[EmotionLabel.NEUTRAL] == 0
        assert result.face_box == (10, 0, 50, 40)
        assert "face_box" not in repr(result)
    finally:
        instance.close()


@pytest.mark.parametrize("logits", [[0] * 6, [[0] * 7], [float("nan")] * 7, [float("inf")] * 7])
def test_invalid_model_outputs_do_not_become_no_face(logits):
    instance = pipeline([FaceBox(0, 0, 10, 10)], logits)
    try:
        with pytest.raises(InferenceError):
            instance.classify(sample())
    finally:
        instance.close()


def test_warmup_exercises_model_even_without_face_and_close_is_idempotent():
    instance = pipeline([])
    instance.warmup()
    assert instance.detector.calls == instance.model.calls == 1
    instance.close()
    instance.close()
    assert instance.detector.closed and instance.model.closed
    with pytest.raises(InferenceError, match="closed"):
        instance.classify(sample())
