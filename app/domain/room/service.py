import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import AppError
from app.db.models import Participant, Room
from app.domain.enums import ConnectionStatus, ParticipantStatus, RoomStatus
from app.domain.user.service import active_room, normalize_nickname

OCCUPYING = (ParticipantStatus.ACTIVE, ParticipantStatus.WAITING_NEXT_GAME)


async def get_room(session: AsyncSession, slug: str, *, lock: bool = False) -> Room:
    query = select(Room).where(Room.invite_slug == slug)
    if lock:
        query = query.with_for_update()
    room = await session.scalar(query)
    if room is None:
        raise AppError("ROOM_NOT_FOUND")
    return room


def require_open(room: Room) -> None:
    if room.status == RoomStatus.FINISHED:
        raise AppError("ROOM_FINISHED")
    if room.status == RoomStatus.CLOSED:
        raise AppError("ROOM_CLOSED")


def require_host(room: Room, user_id: UUID) -> None:
    if room.host_user_id != user_id:
        raise AppError("NOT_HOST")


async def create_room(
    session: AsyncSession, user_id: UUID, round_count: int, time_limit_sec: int
) -> tuple[Room, bool]:
    for _ in range(5):
        statement = (
            insert(Room)
            .values(
                host_user_id=user_id,
                invite_slug=secrets.token_urlsafe(9),
                round_count=round_count,
                time_limit_sec=time_limit_sec,
                created_at=clock.now_utc(),
                last_active_at=clock.now_utc(),
            )
            .on_conflict_do_nothing()
            .returning(Room)
        )
        room = await session.scalar(statement)
        if room is not None:
            return room, False
        existing = await active_room(session, user_id)
        if existing is not None:
            return existing, True
        # A slug collision or concurrent slot release: retry with another random slug.
    raise AppError("SERVICE_UNAVAILABLE")


async def occupied_count(session: AsyncSession, room_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Participant)
            .where(Participant.room_id == room_id, Participant.status.in_(OCCUPYING))
        )
        or 0
    )


async def join_room(session: AsyncSession, room: Room, user_id: UUID, nickname: str) -> Participant:
    """Caller holds the room row lock until transaction commit."""
    require_open(room)
    nickname = normalize_nickname(nickname)
    participant = await session.scalar(
        select(Participant).where(Participant.room_id == room.id, Participant.user_id == user_id)
    )
    if participant is not None and participant.status in OCCUPYING:
        return participant
    occupants = list(
        await session.scalars(
            select(Participant).where(
                Participant.room_id == room.id, Participant.status.in_(OCCUPYING)
            )
        )
    )
    if len(occupants) >= 12:
        raise AppError("ROOM_FULL", detail={"capacity": 12})
    used_colors = {row.color_tag for row in occupants}
    color = (
        participant.color_tag
        if participant is not None and participant.color_tag not in used_colors
        else next(index for index in range(12) if index not in used_colors)
    )
    status = (
        ParticipantStatus.ACTIVE
        if room.status == RoomStatus.WAITING
        else ParticipantStatus.WAITING_NEXT_GAME
    )
    if participant is None:
        participant = Participant(
            room_id=room.id,
            user_id=user_id,
            nickname=nickname,
            color_tag=color,
            status=status,
            connection_status=ConnectionStatus.DISCONNECTED,
            disconnected_at=clock.now_utc(),
            joined_at=clock.now_utc(),
        )
        session.add(participant)
    else:
        participant.nickname = nickname
        participant.color_tag = color
        participant.status = status
        participant.connection_status = ConnectionStatus.DISCONNECTED
        # An entry without a socket is a disconnection in progress, not a live participant.
        participant.disconnected_at = clock.now_utc()
    room.last_active_at = clock.now_utc()
    await session.flush()
    return participant


async def current_participant(session: AsyncSession, room_id: int, user_id: UUID) -> Participant:
    participant = await session.scalar(
        select(Participant).where(
            Participant.room_id == room_id,
            Participant.user_id == user_id,
            Participant.status.in_(OCCUPYING),
        )
    )
    if participant is None:
        raise AppError("NOT_A_PARTICIPANT")
    return participant


async def lobby_participants(session: AsyncSession, room_id: int) -> list[Participant]:
    return list(
        await session.scalars(
            select(Participant)
            .where(Participant.room_id == room_id, Participant.status.in_(OCCUPYING))
            .order_by(Participant.joined_at, Participant.id)
        )
    )


async def lobby_snapshot(
    session: AsyncSession, room: Room, participant: Participant
) -> dict[str, Any]:
    require_open(room)
    if room.status != RoomStatus.WAITING:
        raise AppError("SERVICE_UNAVAILABLE")
    members = await lobby_participants(session, room.id)
    return {
        "room": {
            "slug": room.invite_slug,
            "status": room.status.value,
            "settings": {
                "roundCount": room.round_count,
                "timeLimitSec": room.time_limit_sec,
                "emotionSet": room.emotion_set,
            },
        },
        "me": {
            "participantId": str(participant.id),
            "nickname": participant.nickname,
            "isHost": room.host_user_id == participant.user_id,
            "status": participant.status.value,
            "totalPoints": participant.total_points,
        },
        "participants": [
            {
                "participantId": str(member.id),
                "nickname": member.nickname,
                "colorTag": member.color_tag,
                "connectionStatus": member.connection_status.value,
                "status": member.status.value,
                "isHost": room.host_user_id == member.user_id,
            }
            for member in members
        ],
        "game": None,
        "serverTimeMs": clock.now_ms(),
    }
