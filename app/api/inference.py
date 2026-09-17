from fastapi import APIRouter, Request, Response

from app.api.deps import Runtime, limit_ip
from app.api.schemas import EmotionScore, InferenceResponse
from app.core.errors import AppError
from app.domain.scoring.service import target_score
from app.inference.protocol import InferenceError
from app.media.images import inspect_jpeg
from app.media.multipart import boundary_of, read_capped, read_part

router = APIRouter(tags=["inference"])


@router.post(
    "/api/inference",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["image"],
                        "properties": {
                            "image": {
                                "type": "string",
                                "format": "binary",
                                "description": "JPEG image, up to the configured upload limit",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def infer(request: Request, response: Response, runtime: Runtime) -> InferenceResponse:
    """Run the production emotion model directly for demonstrations and return its result."""
    settings = runtime.settings
    await limit_ip(
        request,
        "inference:ip",
        capacity=settings.inference_api_rate_limit_per_minute,
        window_sec=60,
    )
    boundary = boundary_of(request.headers.get("content-type"))
    image = read_part(await read_capped(request, settings.max_upload_bytes), boundary, "image")
    inspect_jpeg(image, max_bytes=settings.max_upload_bytes, max_pixels=settings.max_image_pixels)

    classifier = runtime.classifier
    if not runtime.initialized or classifier is None:
        raise AppError("SERVICE_UNAVAILABLE")
    try:
        result = await classifier.classify(image)
    except InferenceError:
        raise AppError("SERVICE_UNAVAILABLE") from None
    finally:
        del image

    response.headers["Cache-Control"] = "no-store"
    if not result.face_detected or result.probabilities is None:
        return InferenceResponse(face_detected=False, prediction=None, scores=None)

    scores = {
        label: float(target_score(probability))
        for label, probability in result.probabilities.items()
    }
    top_label = max(result.probabilities, key=result.probabilities.__getitem__)
    return InferenceResponse(
        face_detected=True,
        prediction=EmotionScore(label=top_label, score=scores[top_label]),
        scores=scores,
    )
