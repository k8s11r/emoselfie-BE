"""D-6 participant grace. A slot is held only while an entry is still on its way to a socket."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
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
