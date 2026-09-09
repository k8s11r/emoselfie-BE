from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import capture_token
from app.db.models import Participant, Room, Round
from app.domain.enums import ParticipantStatus, RoomStatus, RoundStatus
from app.domain.game import emotions
from app.domain.room.service import require_open

COUNTDOWN_SEC = 3
MIN_PLAYERS = 2


async def active_participants(session: AsyncSession, room_id: int) -> list[Participant]:
    return list(
        await session.scalars(
            select(Participant)
            .where(Participant.room_id == room_id, Participant.status == ParticipantStatus.ACTIVE)
            .order_by(Participant.joined_at, Participant.id)
        )
    )


async def active_count(session: AsyncSession, room_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Participant)
            .where(Participant.room_id == room_id, Participant.status == ParticipantStatus.ACTIVE)
        )
        or 0
    )


async def open_round(session: AsyncSession, room: Room, index: int) -> Round:
    """§10.1. T0 is the row's creation time; every client deadline is absolute (RD-08)."""
    if not 1 <= index <= len(room.emotion_sequence):
        raise ValueError("The round index must fall inside the stored emotion sequence")
    revealed_at = clock.now_utc()
    current = Round(
        room_id=room.id,
        index=index,
        target_emotion=room.emotion_sequence[index - 1],
        status=RoundStatus.REVEALED,
        revealed_at=revealed_at,
        deadline_at=revealed_at + timedelta(seconds=COUNTDOWN_SEC + room.time_limit_sec),
    )
    session.add(current)
    await session.flush()
    room.current_round_id = current.id
    room.last_active_at = revealed_at
    return current


async def start_game(session: AsyncSession, room: Room) -> Round:
    """§8.2 start. The caller holds the room row lock and checked host permission."""
    require_open(room)
    if room.status != RoomStatus.WAITING:
        raise AppError("GAME_ALREADY_STARTED")
    if await active_count(session, room.id) < MIN_PLAYERS:
        raise AppError("NOT_ENOUGH_PLAYERS")
    # Fixed up front so a voided round can reuse the remainder and restores stay identical (§9.2).
    room.emotion_sequence = list(emotions.build_sequence(room.round_count))
    room.status = RoomStatus.PLAYING
    room.consecutive_voided = 0
    await session.execute(
        update(Participant)
        .where(
            Participant.room_id == room.id,
            Participant.status == ParticipantStatus.WAITING_NEXT_GAME,
        )
        .values(status=ParticipantStatus.ACTIVE)
    )
    return await open_round(session, room, 1)


def started_view(room: Room, participants: list[Participant]) -> dict[str, Any]:
    return {
        "roundCount": room.round_count,
        "timeLimitSec": room.time_limit_sec,
        "participantIds": [str(participant.id) for participant in participants],
    }


def revealed_view(
    room: Room, current: Round, participant: Participant, active_total: int, settings: Settings
) -> dict[str, Any]:
    """Personal event: the capture token differs per participant (§6.3, §13.2)."""
    deadline_ms = clock.to_epoch_ms(current.deadline_at)
    return {
        "roundId": str(current.id),
        "index": current.index,
        "roundCount": room.round_count,
        "emotion": emotions.describe(current.target_emotion).view(),
        "countdownEndsAtMs": clock.to_epoch_ms(current.revealed_at) + COUNTDOWN_SEC * 1000,
        "deadlineAtMs": deadline_ms,
        "captureToken": capture_token(
            current.id,
            participant.id,
            deadline_ms,
            settings.capture_token_secret.get_secret_value(),
        ),
        "activeCount": active_total,
    }
