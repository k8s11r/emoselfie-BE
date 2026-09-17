from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app.api import inference as inference_api
from app.api.errors import install_error_handlers
from app.domain.enums import EmotionLabel
from app.inference.fake import FakeClassifier


def sample_jpeg() -> bytes:
    with Image.new("RGB", (32, 32), (200, 140, 90)) as image, BytesIO() as output:
        image.save(output, format="JPEG")
        return output.getvalue()


def app_with(classifier: FakeClassifier) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(inference_api.router)
    app.state.resources = SimpleNamespace(
        initialized=True,
        classifier=classifier,
        settings=SimpleNamespace(
            inference_api_rate_limit_per_minute=10,
            max_upload_bytes=2_097_152,
            max_image_pixels=4_194_304,
        ),
    )
    return app


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    monkeypatch.setattr(inference_api, "limit_ip", allow)


async def post_image(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/inference", files={"image": ("face.jpg", sample_jpeg(), "image/jpeg")}
        )


async def test_inference_api_returns_scores_after_model_completion():
    response = await post_image(app_with(FakeClassifier()))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "faceDetected": True,
        "prediction": {"label": "happy", "score": 100.0},
        "scores": {
            label.value: (100.0 if label == EmotionLabel.HAPPY else 0.0) for label in EmotionLabel
        },
    }


async def test_inference_api_returns_no_face_without_scores():
    response = await post_image(app_with(FakeClassifier("no_face")))

    assert response.status_code == 200
    assert response.json() == {"faceDetected": False, "prediction": None, "scores": None}


async def test_inference_api_maps_engine_failure_to_service_unavailable():
    response = await post_image(app_with(FakeClassifier("failed")))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_inference_api_openapi_exposes_image_file_picker():
    operation = app_with(FakeClassifier()).openapi()["paths"]["/api/inference"]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    image = request_body["content"]["multipart/form-data"]["schema"]["properties"]["image"]
    assert image["type"] == "string"
    assert image["format"] == "binary"
