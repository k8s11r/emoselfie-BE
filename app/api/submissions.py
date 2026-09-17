from fastapi import APIRouter, Request, Response

from app.api.deps import CurrentUser, Runtime
from app.api.schemas import SubmissionAccepted
from app.core import clock
from app.core.errors import AppError
from app.core.ratelimit import enforce_limit
from app.core.redis import ROUND_TTL_SEC
from app.db.models import Round
from app.domain.enums import RoomStatus
from app.domain.room import service as rooms
from app.domain.round import service
from app.media.images import inspect_jpeg
from app.media.multipart import boundary_of, read_capped, read_part

router = APIRouter(prefix="/api/rooms", tags=["submissions"])


@router.post("/{slug}/rounds/{round_id}/submissions", status_code=202)
async def submit(
    slug: str,
    round_id: int,
    request: Request,
    response: Response,
    user: CurrentUser,
    runtime: Runtime,
) -> SubmissionAccepted:
    # ① The receive time is taken before any body work, so a slow upload cannot buy time (CP-08).
    received_at_ms = clock.now_ms()
    settings = runtime.settings
    boundary = boundary_of(request.headers.get("content-type"))
    # §16: five upload attempts per participant per round, the bucket keyed by both.
    await enforce_limit(
        runtime.redis,
        "upload:participant",
        f"{round_id}:{user.uuid}",
        capacity=5,
        window_sec=ROUND_TTL_SEC,
    )
    async with runtime.sessions.begin() as session:
        room = await rooms.get_room(session, slug)
        participant = await rooms.current_participant(session, room.id, user.uuid)
        current = await session.get(Round, round_id)
        if (
            current is None
            or current.room_id != room.id
            or room.status != RoomStatus.PLAYING
            or room.current_round_id != current.id
        ):
            raise AppError("NOT_CURRENT_ROUND")
        if current.status not in service.OPEN_STATUSES:
            raise AppError("DEADLINE_PASSED")
        participant_id, room_id = participant.id, room.id
        # ③~⑤ before the body: a late or duplicate request consumes no submission slot.
        await service.accept_submission(
            runtime.redis,
            current,
            participant_id,
            received_at_ms,
            request.headers.get("x-capture-token"),
            settings.capture_token_secret.get_secret_value(),
        )
        row = await service.create_submission_row(
            session, current.id, participant_id, received_at_ms
        )
        submission_id = row.id

    image = read_part(await read_capped(request, settings.max_upload_bytes), boundary, "image")
    inspect_jpeg(image, max_bytes=settings.max_upload_bytes, max_pixels=settings.max_image_pixels)
    await runtime.rounds.dispatch(round_id, room_id, participant_id, submission_id, image)
    response.headers["Cache-Control"] = "no-store"
    return SubmissionAccepted(submission_id=submission_id, accepted_at_ms=received_at_ms)
