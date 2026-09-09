from fastapi import APIRouter, Response

from app.api.deps import Runtime, SessionIdentity
from app.core import clock
from app.core.errors import AppError
from app.core.redis import image_key
from app.core.security import verify_media_token
from app.db.models import Participant, Submission

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{token}")
async def serve(token: str, viewer_user_id: SessionIdentity, runtime: Runtime) -> Response:
    """§8.4 D-1. A leaked link is useless: the token names the single viewer it was issued to."""
    claims = verify_media_token(token, runtime.settings.media_token_secret.get_secret_value())
    if claims is None:
        raise AppError("NOT_A_VIEWER")
    round_id, submission_id, viewer_id, expires_at_ms = claims
    if clock.now_ms() >= expires_at_ms:
        raise AppError("MEDIA_EXPIRED")
    async with runtime.sessions() as session:
        viewer = await session.get(Participant, viewer_id)
        if viewer is None or viewer.user_id != viewer_user_id:
            raise AppError("NOT_A_VIEWER")
        row = await session.get(Submission, submission_id)
        if row is None or row.round_id != round_id:
            raise AppError("NOT_A_VIEWER")
        owner_id = row.participant_id
    image = await runtime.media_redis.get(image_key(round_id, owner_id))
    if not image:
        # Every end of a round deletes the key, so this is the normal expiry path (PV-01).
        raise AppError("MEDIA_EXPIRED")
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
