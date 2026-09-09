"""BE-011·012 실제 모델 대조. `--run-model`과 준비된 아티팩트가 있어야 실행한다."""

import os
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.domain.enums import EmotionLabel
from app.inference.loader import (
    MODEL_LABELS,
    MODEL_PREPROCESSING,
    load_classifier,
    load_real_pipeline,
    verify_artifact,
)
from app.inference.pipeline import preprocess
from app.inference.protocol import InferenceError
from app.media.images import decode_jpeg

pytestmark = pytest.mark.model

MODEL_DIR = Path(os.environ.get("MODEL_DIR", ".models"))
EMOTION_ARTIFACT = "FER_static_ResNet50_AffectNet.pt"
FACE_ARTIFACT = "blaze_face_short_range.tflite"
# 저자가 공개한 데모 이미지. `scripts/prepare_models.py --with-example`로 받는다.
REFERENCE_IMAGE = MODEL_DIR / "fig1.jpg"


@pytest.fixture
def model_settings(settings):
    settings.inference_backend = "real"
    settings.emotion_model_path = MODEL_DIR / EMOTION_ARTIFACT
    settings.face_model_path = MODEL_DIR / FACE_ARTIFACT
    return settings


@pytest.fixture
def pipeline(model_settings):
    engine = load_real_pipeline(model_settings)
    try:
        yield engine
    finally:
        engine.close()


def upstream_preprocessing(face: Image.Image) -> np.ndarray:
    """ElenaRyumina/Facial_Expression_Recognition의 `pth_processing`을 그대로 옮긴 기준 구현(MIT).

    torch 연산으로 독립 계산해 우리의 numpy 경로와 교차 검증한다.
    """
    import torch

    with face.resize((224, 224), resample=Image.Resampling.NEAREST) as resized:
        pixels = np.array(resized, dtype=np.uint8)
    tensor = torch.from_numpy(pixels).permute(2, 0, 1).to(torch.float32)
    tensor = torch.flip(tensor, dims=(0,))
    tensor[0, :, :] -= 91.4953
    tensor[1, :, :] -= 103.8827
    tensor[2, :, :] -= 131.0912
    return torch.unsqueeze(tensor, 0).numpy()


def test_prepared_artifacts_match_the_pinned_checksums():
    verify_artifact(MODEL_DIR / EMOTION_ARTIFACT, EMOTION_ARTIFACT)
    verify_artifact(MODEL_DIR / FACE_ARTIFACT, FACE_ARTIFACT)


def test_verification_rejects_a_same_size_modified_artifact(tmp_path):
    original = (MODEL_DIR / FACE_ARTIFACT).read_bytes()
    tampered = tmp_path / FACE_ARTIFACT
    tampered.write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))
    assert tampered.stat().st_size == len(original)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_artifact(tampered, FACE_ARTIFACT)


def test_preprocessing_matches_the_upstream_reference():
    with decode_jpeg(REFERENCE_IMAGE.read_bytes()) as image:
        ours = preprocess(image, MODEL_PREPROCESSING)
        reference = upstream_preprocessing(image)
    assert ours.shape == (1, 3, 224, 224)
    # 동일 연산이므로 허용 오차 없이 일치해야 한다. /255나 ImageNet 정규화를 쓰면 즉시 어긋난다.
    assert np.array_equal(ours, reference)


def test_reference_image_is_classified_as_happy(pipeline):
    result = pipeline.classify(REFERENCE_IMAGE.read_bytes())

    assert result.face_detected
    assert result.probabilities is not None
    assert set(result.probabilities) == set(EmotionLabel)
    assert MODEL_LABELS[1] is EmotionLabel.HAPPY
    top = max(result.probabilities, key=lambda label: result.probabilities[label])
    assert top is EmotionLabel.HAPPY
    # 저자 데모와 같은 판정. 라벨 순서가 밀리면 다른 감정이 최대가 된다.
    assert result.probabilities[EmotionLabel.HAPPY] > 0.9

    assert result.face_box is not None
    x, y, width, height = result.face_box
    with decode_jpeg(REFERENCE_IMAGE.read_bytes()) as image:
        assert 0 <= x and 0 <= y and x + width <= image.width and y + height <= image.height
    assert width > 32 and height > 32


def test_image_without_a_face_is_not_an_engine_failure(pipeline):
    noise = np.random.default_rng(20260908).integers(0, 256, (480, 640, 3), dtype=np.uint8)
    with Image.fromarray(noise) as image, BytesIO() as buffer:
        image.save(buffer, format="JPEG", quality=90)
        data = buffer.getvalue()

    result = pipeline.classify(data)

    assert result.face_detected is False
    assert result.probabilities is None
    assert result.face_box is None


def test_warmup_and_close_release_the_real_engine(pipeline):
    pipeline.warmup()
    pipeline.close()

    with pytest.raises(InferenceError):
        pipeline.classify(REFERENCE_IMAGE.read_bytes())
    pipeline.close()


async def test_real_runner_starts_warms_up_and_closes(model_settings):
    classifier = await load_classifier(model_settings)
    try:
        result = await classifier.classify(REFERENCE_IMAGE.read_bytes())
    finally:
        await classifier.aclose()

    assert result.face_detected
    assert result.probabilities is not None
    assert max(result.probabilities, key=lambda label: result.probabilities[label]) is (
        EmotionLabel.HAPPY
    )
    with pytest.raises(InferenceError):
        await classifier.classify(REFERENCE_IMAGE.read_bytes())
