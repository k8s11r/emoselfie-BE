import asyncio

import pytest
from pydantic import ValidationError

from app.domain.enums import EmotionLabel
from app.inference.fake import FakeClassifier
from app.inference.loader import load_classifier
from app.inference.protocol import EmotionResult, InferenceError


async def test_fake_success_is_deterministic_and_does_not_share_mutable_results():
    classifier = FakeClassifier()
    first = await classifier.classify(b"synthetic input")
    assert first.face_detected
    assert first.probabilities[EmotionLabel.HAPPY] == 1
    first.probabilities[EmotionLabel.HAPPY] = 0
    assert (await classifier.classify(b"different input")).probabilities[EmotionLabel.HAPPY] == 1


async def test_no_face_is_distinct_from_engine_failure():
    result = await FakeClassifier("no_face").classify(b"input")
    assert result == EmotionResult(face_detected=False)
    with pytest.raises(InferenceError):
        await FakeClassifier("failed").classify(b"input")


async def test_timeout_fake_is_cancellable():
    task = asyncio.create_task(FakeClassifier("timeout").classify(b"input"))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.01)
    assert task.cancelled()


@pytest.mark.parametrize(
    "values",
    [
        {"face_detected": True},
        {"face_detected": True, "probabilities": {"happy": 1}},
        {"face_detected": True, "probabilities": {label: 0.2 for label in EmotionLabel}},
        {"face_detected": False, "face_box": (1, 2, 3, 4)},
        {"face_detected": False, "probabilities": {"happy": 1}},
        {"face_detected": True, "probabilities": {label: float("nan") for label in EmotionLabel}},
    ],
)
def test_invalid_inference_result(values):
    with pytest.raises(ValidationError):
        EmotionResult(**values)


async def test_real_loader_never_falls_back(settings, tmp_path):
    settings.inference_backend = "real"
    settings.emotion_model_path = tmp_path / "emotion.pt"
    settings.face_model_path = tmp_path / "face.tflite"
    with pytest.raises(RuntimeError, match="artifacts are missing"):
        await load_classifier(settings)
    settings.emotion_model_path.touch()
    settings.face_model_path.touch()
    with pytest.raises(RuntimeError, match="artifact size mismatch"):
        await load_classifier(settings)
