"""D-6 participant grace. A slot is held only while an entry is still on its way to a socket."""

from collections.abc import Awaitable
from datetime import timedelta
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.redis import pod_key, socket_key, user_socket_key
from app.db.models import Participant, Room
from app.domain.enums import ConnectionStatus, ParticipantStatus, RoomStatus


def grace_expired(participant: Participant, grace_sec: int) -> bool:
    """A participant who joined but never opened a socket is disconnected from the start.

    §10.4 schedules this job on disconnect; an entry that never connected has the same shape,
    so the clock starts when the record is written. Without it a client that cannot keep its
    cookie fills the room with entries that can never connect (§6.2).
    """
    if participant.connection_status == ConnectionStatus.CONNECTED:
        return False
    if participant.status != ParticipantStatus.ACTIVE:
        return participant.status == ParticipantStatus.WAITING_NEXT_GAME
    since = participant.disconnected_at or participant.joined_at
    return since + timedelta(seconds=grace_sec) <= clock.now_utc()


def release(room: Room, participant: Participant) -> bool:
    """Returns the capacity slot and the colour. Points survive for a later return (PM-11)."""
    if participant.status == ParticipantStatus.LEFT:
        return False
    participant.status = ParticipantStatus.LEFT
    participant.connection_status = ConnectionStatus.DISCONNECTED
    room.last_active_at = clock.now_utc()
    return True


async def active_shortfall(session: AsyncSession, room: Room) -> bool:
    """§10.6 D-6: a playing room with fewer than two active participants ends now."""
    if room.status != RoomStatus.PLAYING:
        return False
    from app.domain.game.service import MIN_PLAYERS, active_count

    return await active_count(session, room.id) < MIN_PLAYERS


async def socket_is_live(client: Redis, user_id: UUID, participant_id: int) -> bool:
    """A socket counts as live only while the Pod that accepted it still says it is running.

    A crashed Pod leaves `user:sock` and `sock` keys behind until their TTL, so key existence
    alone would keep a seat occupied by a connection nobody holds any more (G-09).
    """
    sid = await cast(Awaitable[bytes | None], client.get(user_socket_key(str(user_id))))
    if not sid:
        return False
    context = await cast(Awaitable[dict[bytes, bytes]], client.hgetall(socket_key(sid.decode())))
    if not context or context.get(b"participantId") != str(participant_id).encode():
        return False
    owner = context.get(b"podId")
    if owner is None:
        # Every connection this code accepts records its Pod, so a record without one was
        # written by a process that is no longer running. Its socket cannot still be held.
        return False
    return bool(await cast(Awaitable[int], client.exists(pod_key(owner.decode()))))


async def connected_without_socket(session: AsyncSession) -> list[Participant]:
    """Participants an open room still counts as connected, for a liveness re-check."""
    return list(
        await session.scalars(
            select(Participant)
            .join(Room, Room.id == Participant.room_id)
            .where(
                Room.status.in_((RoomStatus.WAITING, RoomStatus.PLAYING)),
                Participant.status.in_(
                    (ParticipantStatus.ACTIVE, ParticipantStatus.WAITING_NEXT_GAME)
                ),
                Participant.connection_status == ConnectionStatus.CONNECTED,
            )
        )
    )
