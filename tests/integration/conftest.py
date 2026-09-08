import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core import clock
from app.db.models import Participant, Room, Round, User
from app.db.session import create_engine, session_factory
from app.domain.enums import EmotionLabel


@pytest.fixture
async def database(settings):
    engine = create_engine(settings)
    try:
        yield session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def game_rows(database):
    user_ids = [uuid4(), uuid4()]
    try:
        async with database.begin() as session:
            session.add_all([User(uuid=uid) for uid in user_ids])
            await session.flush()
            room = Room(host_user_id=user_ids[0], invite_slug=secrets.token_urlsafe(9))
            session.add(room)
            await session.flush()
            players = [
                Participant(room_id=room.id, user_id=uid, nickname="테스터", color_tag=index)
                for index, uid in enumerate(user_ids)
            ]
            round_ = Round(
                room_id=room.id,
                index=1,
                target_emotion=EmotionLabel.HAPPY,
                revealed_at=clock.now_utc(),
                deadline_at=clock.now_utc() + timedelta(seconds=23),
            )
            session.add_all([*players, round_])
            await session.flush()
            room.current_round_id = round_.id
        yield room, players, round_
    finally:
        # Only this test's rows. Never truncate a developer's database.
        async with database.begin() as session:
            await session.execute(delete(Room).where(Room.host_user_id.in_(user_ids)))
            await session.execute(delete(User).where(User.uuid.in_(user_ids)))
