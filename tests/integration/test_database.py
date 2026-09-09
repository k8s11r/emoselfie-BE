import asyncio
import secrets

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core import clock
from app.db.models import Participant, Reaction, Room, Round, RoundSkip, Submission, User
from app.domain.enums import ReactionType, RoomStatus, SubmissionStatus

pytestmark = pytest.mark.integration


async def test_concurrent_owner_creation_and_finished_slot_release(database, game_rows):
    room, _, _ = game_rows
    async with database.begin() as session:
        await session.execute(
            update(Room).where(Room.id == room.id).values(status=RoomStatus.FINISHED)
        )

    async def create():
        try:
            async with database.begin() as session:
                session.add(
                    Room(host_user_id=room.host_user_id, invite_slug=secrets.token_urlsafe(9))
                )
            return "created"
        except IntegrityError as exc:
            assert "uq_room_active_owner" in str(exc.orig)
            return "conflict"

    assert sorted(await asyncio.gather(create(), create())) == ["conflict", "created"]


async def test_current_round_must_belong_to_room(database, game_rows):
    room, players, round_ = game_rows
    with pytest.raises(IntegrityError, match="fk_room_current_round"):
        async with database.begin() as session:
            other = Room(
                host_user_id=players[1].user_id,
                invite_slug=secrets.token_urlsafe(9),
                current_round_id=round_.id,
            )
            session.add(other)
    with pytest.raises(IntegrityError, match="fk_room_current_round"):
        async with database.begin() as session:
            await session.execute(delete(Round).where(Round.id == room.current_round_id))


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"round_count": 4}, "round_count"),
        ({"time_limit_sec": 45}, "time_limit"),
        ({"emotion_set": "easy"}, "emotion_set"),
        ({"invite_slug": "short"}, "slug_len"),
    ],
)
async def test_room_constraints(database, game_rows, changes, constraint):
    room, _, _ = game_rows
    with pytest.raises(IntegrityError, match=constraint):
        async with database.begin() as session:
            await session.execute(update(Room).where(Room.id == room.id).values(**changes))


async def test_round_deadline_and_participant_uniqueness(database, game_rows):
    room, players, round_ = game_rows
    with pytest.raises(IntegrityError, match="deadline"):
        async with database.begin() as session:
            await session.execute(
                update(Round).where(Round.id == round_.id).values(deadline_at=round_.revealed_at)
            )
    with pytest.raises(IntegrityError, match="uq_room_user"):
        async with database.begin() as session:
            session.add(
                Participant(
                    room_id=room.id, user_id=players[0].user_id, nickname="중복", color_tag=2
                )
            )


@pytest.mark.parametrize(
    "values,constraint",
    [
        ({"status": SubmissionStatus.MISSED, "received_at": clock.from_epoch_ms(1)}, "received"),
        ({"status": SubmissionStatus.SUBMITTED, "received_at": None}, "received"),
        ({"status": SubmissionStatus.MISSED, "target_score": 101}, "score_range"),
    ],
)
async def test_submission_constraints(database, game_rows, values, constraint):
    _, players, round_ = game_rows
    with pytest.raises(IntegrityError, match=constraint):
        async with database.begin() as session:
            session.add(Submission(round_id=round_.id, participant_id=players[0].id, **values))


async def test_unique_reactions_submissions_and_cascade(database, game_rows):
    room, players, round_ = game_rows
    async with database.begin() as session:
        submission = Submission(
            round_id=round_.id,
            participant_id=players[0].id,
            status=SubmissionStatus.NO_FACE,
            received_at=clock.now_utc(),
            target_score=0,
            rank_points=30,
        )
        session.add(submission)
        await session.flush()
        session.add_all(
            [
                Reaction(
                    round_id=round_.id,
                    actor_participant_id=players[1].id,
                    target_submission_id=submission.id,
                    type=kind,
                )
                for kind in ReactionType
            ]
        )
        session.add(RoundSkip(round_id=round_.id, participant_id=players[1].id))

    for row, constraint in [
        (
            Submission(
                round_id=round_.id,
                participant_id=players[0].id,
                status=SubmissionStatus.MISSED,
            ),
            "uq_round_participant",
        ),
        (
            Reaction(
                round_id=round_.id,
                actor_participant_id=players[1].id,
                target_submission_id=submission.id,
                type=ReactionType.LIKE,
            ),
            "uq_reaction",
        ),
        (RoundSkip(round_id=round_.id, participant_id=players[1].id), "pk_round_skips"),
    ]:
        with pytest.raises(IntegrityError, match=constraint):
            async with database.begin() as session:
                session.add(row)

    # A current-round cycle must not block deleting the parent room.
    async with database.begin() as session:
        await session.execute(delete(Room).where(Room.id == room.id))
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for model, condition in [
            (Participant, Participant.room_id == room.id),
            (Round, Round.room_id == room.id),
            (Submission, Submission.round_id == round_.id),
            (Reaction, Reaction.round_id == round_.id),
            (RoundSkip, RoundSkip.round_id == round_.id),
        ]:
            assert (
                await session.scalar(select(func.count()).select_from(model).where(condition)) == 0
            )
        assert await session.get(User, room.host_user_id) is not None
