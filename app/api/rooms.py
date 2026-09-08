from fastapi import APIRouter, Request, Response

from app.api.deps import CurrentUser, Runtime, limit_ip
from app.api.schemas import (
    CreatedRoom,
    LobbyState,
    NicknameInput,
    ParticipantJoined,
    RoomPreview,
    RoomSettings,
    SettingsInput,
    SettingsPatch,
)
from app.core import clock
from app.core.errors import AppError
from app.core.ratelimit import enforce_limit
from app.db.models import Room
from app.domain.enums import RoomStatus
from app.domain.room import service

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def settings_view(room: Room) -> RoomSettings:
    return RoomSettings.model_validate(
        {
            "roundCount": room.round_count,
            "timeLimitSec": room.time_limit_sec,
        }
    )


@router.post("", response_model=CreatedRoom)
async def create(
    body: SettingsInput, request: Request, response: Response, user: CurrentUser, runtime: Runtime
) -> CreatedRoom:
    await limit_ip(request, "room:create:ip", 10, 3600)
    await enforce_limit(
        runtime.redis, "room:create:user", str(user.uuid), capacity=5, window_sec=3600
    )
    async with runtime.sessions.begin() as session:
        room, existing = await service.create_room(
            session, user.uuid, body.round_count, body.time_limit_sec
        )
        result = CreatedRoom(
            slug=room.invite_slug,
            status=room.status,
            settings=settings_view(room),
            existing=existing,
        )
    response.status_code = 200 if existing else 201
    return result


@router.get("/{slug}")
async def preview(slug: str, request: Request, user: CurrentUser, runtime: Runtime) -> RoomPreview:
    await limit_ip(request, "room:preview:ip", 30, 60)
    async with runtime.sessions() as session:
        room = await service.get_room(session, slug)
        service.require_open(room)
        return RoomPreview(
            status=room.status,
            is_full=await service.occupied_count(session, room.id) >= 12,
            is_host=room.host_user_id == user.uuid,
            settings=SettingsInput.model_validate(
                {
                    "roundCount": room.round_count,
                    "timeLimitSec": room.time_limit_sec,
                }
            ),
        )


@router.post("/{slug}/participants", status_code=201)
async def join(
    slug: str, body: NicknameInput, request: Request, user: CurrentUser, runtime: Runtime
) -> ParticipantJoined:
    await limit_ip(request, "room:join:ip", 20, 60)
    async with runtime.sessions.begin() as session:
        room = await service.get_room(session, slug, lock=True)
        participant = await service.join_room(session, room, user.uuid, body.nickname)
        result = ParticipantJoined(
            participant_id=participant.id,
            nickname=participant.nickname,
            color_tag=participant.color_tag,
            status=participant.status,
            is_host=room.host_user_id == user.uuid,
        )
        room_id = room.id
    if runtime.realtime is not None:
        await runtime.realtime.refresh_lobby(room_id)
    return result


@router.patch("/{slug}/settings")
async def update_settings(
    slug: str, body: SettingsPatch, user: CurrentUser, runtime: Runtime
) -> RoomSettings:
    if not body.model_fields_set or any(
        getattr(body, field) is None for field in body.model_fields_set
    ):
        raise AppError("INVALID_REQUEST")
    async with runtime.sessions.begin() as session:
        room = await service.get_room(session, slug, lock=True)
        service.require_host(room, user.uuid)
        service.require_open(room)
        if room.status != RoomStatus.WAITING:
            raise AppError("GAME_ALREADY_STARTED")
        if body.round_count is not None:
            room.round_count = body.round_count
        if body.time_limit_sec is not None:
            room.time_limit_sec = body.time_limit_sec
        room.last_active_at = clock.now_utc()
        result = settings_view(room)
        room_id = room.id
    if runtime.realtime is not None:
        await runtime.realtime.refresh_lobby(room_id)
    return result


@router.get("/{slug}/state")
async def lobby_state(slug: str, user: CurrentUser, runtime: Runtime) -> LobbyState:
    async with runtime.sessions.begin() as session:
        # Serialize snapshot reads against lobby mutations to avoid a mixed version.
        room = await service.get_room(session, slug, lock=True)
        participant = await service.current_participant(session, room.id, user.uuid)
        return LobbyState.model_validate(await service.lobby_snapshot(session, room, participant))


@router.post("/{slug}/close")
async def close(slug: str, user: CurrentUser, runtime: Runtime) -> dict[str, bool]:
    async with runtime.sessions.begin() as session:
        room = await service.get_room(session, slug, lock=True)
        service.require_host(room, user.uuid)
        if room.status == RoomStatus.CLOSED:
            return {"ok": True}
        if room.status != RoomStatus.WAITING:
            raise AppError("GAME_ALREADY_STARTED")
        room.status = RoomStatus.CLOSED
        room.last_active_at = clock.now_utc()
        room_id = room.id
    if runtime.realtime is not None:
        await runtime.realtime.refresh_lobby(room_id)
    return {"ok": True}
