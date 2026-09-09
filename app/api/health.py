from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.resources import Resources

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    resources: Resources = request.app.state.resources
    checks = await resources.dependency_checks()
    checks["inference"] = resources.initialized and resources.classifier is not None
    healthy = all(checks.values())
    return JSONResponse(
        {
            "status": "ready" if healthy else "not_ready",
            "inferenceBackend": resources.settings.inference_backend,
            "checks": checks,
        },
        status_code=200 if healthy else 503,
        headers={"Cache-Control": "no-store"},
    )
