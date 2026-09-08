import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import Settings
from app.domain.enums import EmotionLabel
from app.inference.fake import FakeClassifier
from app.inference.pipeline import EmotionPipeline, PreprocessingSpec
from app.inference.protocol import ManagedEmotionClassifier
from app.inference.runner import InferenceRunner

MODEL_LABELS = (
    EmotionLabel.NEUTRAL,
    EmotionLabel.HAPPY,
    EmotionLabel.SAD,
    EmotionLabel.SURPRISE,
    EmotionLabel.FEAR,
    EmotionLabel.DISGUST,
    EmotionLabel.ANGRY,
)
MODEL_PREPROCESSING = PreprocessingSpec(
    bgr_mean=(91.4953, 103.8827, 131.0912),
    resampling="nearest",
)
ARTIFACTS_PATH = Path(__file__).with_name("artifacts.json")


def verify_artifact(path: Path, name: str) -> None:
    artifact = json.loads(ARTIFACTS_PATH.read_text())[name]
    try:
        if path.stat().st_size != artifact["bytes"]:
            raise RuntimeError("Model artifact size mismatch")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError:
        raise RuntimeError("Required model artifacts are missing (BE-DEC-01)") from None
    if digest != artifact["sha256"]:
        raise RuntimeError("Model artifact checksum mismatch")


def load_real_pipeline(settings: Settings) -> EmotionPipeline:
    if settings.emotion_model_version != "v1" or settings.face_model_version != "v1":
        raise RuntimeError("Unsupported model version; update the artifact contract first")
    verify_artifact(settings.emotion_model_path, "FER_static_ResNet50_AffectNet.pt")
    verify_artifact(settings.face_model_path, "blaze_face_short_range.tflite")
    try:
        from app.inference.backends import MediaPipeFaceDetector, TorchEmotionModel
    except ImportError:
        raise RuntimeError(
            "Real inference dependencies are missing; install the inference extra"
        ) from None
    model = TorchEmotionModel(settings.emotion_model_path, threads=settings.inference_torch_threads)
    try:
        detector = MediaPipeFaceDetector(settings.face_model_path)
    except BaseException:
        model.close()
        raise
    return EmotionPipeline(
        detector,
        model,
        labels=MODEL_LABELS,
        preprocessing=MODEL_PREPROCESSING,
        max_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_image_pixels,
    )


async def load_classifier(settings: Settings) -> ManagedEmotionClassifier:
    if settings.inference_backend == "fake" and settings.app_env in {"development", "test"}:
        runner = InferenceRunner(
            FakeClassifier().classify_sync,
            concurrency=settings.inference_concurrency,
            queue_capacity=settings.inference_queue_capacity,
            timeout_sec=settings.inference_timeout_sec,
            max_bytes=settings.max_upload_bytes,
        )
    else:
        # Construction and warmup never block FastAPI's event loop or fetch network resources.
        construction = asyncio.create_task(asyncio.to_thread(load_real_pipeline, settings))
        try:
            pipeline = await asyncio.shield(construction)
        except asyncio.CancelledError:
            try:
                pipeline = await construction
            except Exception:
                pass
            else:
                await asyncio.to_thread(pipeline.close)
            raise
        runner = InferenceRunner(
            pipeline.classify,
            concurrency=settings.inference_concurrency,
            queue_capacity=settings.inference_queue_capacity,
            timeout_sec=settings.inference_timeout_sec,
            max_bytes=settings.max_upload_bytes,
            warmup=pipeline.warmup,
            close=pipeline.close,
        )
    await runner.start()
    return runner
