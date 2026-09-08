import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import AppError
from app.db.models import Room, User
from app.domain.enums import RoomStatus


def normalize_nickname(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if not 2 <= len(value) <= 10 or any(
        not (character.isalnum() or character == " ") for character in value
    ):
        raise AppError("INVALID_NICKNAME")
    return value


async def ensure_user(session: AsyncSession, user_id: UUID) -> User:
    statement = (
        insert(User)
        .values(uuid=user_id, last_seen_at=clock.now_utc())
        .on_conflict_do_update(index_elements=[User.uuid], set_={"last_seen_at": clock.now_utc()})
        .returning(User)
    )
    return (await session.scalars(statement)).one()


async def active_room(session: AsyncSession, user_id: UUID) -> Room | None:
    result: Room | None = await session.scalar(
        select(Room).where(
            Room.host_user_id == user_id, Room.status.in_([RoomStatus.WAITING, RoomStatus.PLAYING])
        )
    )
    return result
