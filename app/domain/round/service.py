"""§10.2·10.3·10.5·10.6 round lifecycle. Callers hold the room row lock for the whole transition."""

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.redis import ROUND_TTL_SEC, image_key, round_key
from app.db.models import Participant, Room, Round, Submission
from app.domain.enums import ParticipantStatus, RoomStatus, RoundStatus, SubmissionStatus
from app.domain.game.service import MIN_PLAYERS, active_participants
from app.domain.scoring.service import (
    ParticipantTotals,
    RoundOutcome,
    RoundSubmission,
    ScoredSubmission,
    accumulate,
    final_ranking,
    most_loved,
    score_round,
)

SCORING_GUARD_SEC = 8
VIEWING_BASE_SEC = 10
VIEWING_PER_VIEWER_SEC = 2
VIEWING_MAX_SEC = 60
MAX_CONSECUTIVE_VOIDED = 3
OPEN_STATUSES = (RoundStatus.REVEALED, RoundStatus.CAPTURING)


def viewing_seconds(viewers: int) -> int:
    """RS-08. The viewer count is the round's result audience, matching the skip denominator."""
    return min(VIEWING_MAX_SEC, VIEWING_BASE_SEC + viewers * VIEWING_PER_VIEWER_SEC)


async def read_submissions(
    client: Redis, round_id: int, active: list[Participant]
) -> tuple[tuple[RoundSubmission, ...], dict[int, dict[str, Any]]]:
    """In-flight round state lives in Redis until the finalize transaction commits it (D-8)."""
    received = await cast(
        Awaitable[dict[bytes, bytes]], client.hgetall(round_key(round_id, "submitted"))
    )
    scored = await cast(
        Awaitable[dict[bytes, bytes]], client.hgetall(round_key(round_id, "scores"))
    )
    submissions: list[RoundSubmission] = []
    details: dict[int, dict[str, Any]] = {}
    for raw_id, raw_time in received.items():
        participant_id = int(raw_id)
        payload = json.loads(scored[raw_id]) if raw_id in scored else {"status": "failed"}
        details[participant_id] = payload
        target = payload.get("targetScore")
        status = SubmissionStatus(payload["status"])
        submissions.append(
            RoundSubmission(
                participant_id=participant_id,
                status=status,
                received_at_ms=int(raw_time),
                target_score=(
                    Decimal(str(target))
                    if status == SubmissionStatus.SUBMITTED and target is not None
                    else None
                ),
            )
        )
    submitted_ids = {submission.participant_id for submission in submissions}
    submissions.extend(
        # RD-06: everyone still active at the deadline without a submission is missed.
        RoundSubmission(participant_id=member.id, status=SubmissionStatus.MISSED)
        for member in active
        if member.id not in submitted_ids
    )
    return tuple(submissions), details


async def close_submissions(session: AsyncSession, current: Round) -> bool:
    """§10.2. The deadline job and a full house race here; only one transition may win."""
    if current.status not in OPEN_STATUSES:
        return False
    current.status = RoundStatus.SCORING
    return True


async def pending_inference(client: Redis, round_id: int) -> int:
    received = await cast(Awaitable[int], client.hlen(round_key(round_id, "submitted")))
    resolved = await cast(Awaitable[int], client.hlen(round_key(round_id, "scores")))
    return received - resolved


async def force_pending_failed(client: Redis, round_id: int) -> int:
    """§10.3 safety net: the deadline + 8s guard turns unresolved work into failures."""
    received = await cast(Awaitable[list[bytes]], client.hkeys(round_key(round_id, "submitted")))
    scored = await cast(Awaitable[list[bytes]], client.hkeys(round_key(round_id, "scores")))
    stranded = [key for key in received if key not in set(scored)]
    if stranded:
        await cast(
            Awaitable[int],
            client.hset(
                round_key(round_id, "scores"),
                mapping={key: json.dumps({"status": "failed"}) for key in stranded},
            ),
        )
        await cast(Awaitable[bool], client.expire(round_key(round_id, "scores"), ROUND_TTL_SEC))
    return len(stranded)


@dataclass(frozen=True, slots=True)
class Finalized:
    outcome: RoundOutcome
    rows: dict[int, int]
    totals: dict[int, int]
    viewers: tuple[int, ...]
    missed: tuple[int, ...]
    viewing_ends_at_ms: int | None


async def finalize_round(
    session: AsyncSession, client: Redis, room: Room, current: Round
) -> Finalized:
    """§10.3·11.4 in one transaction: submissions, points and the round's own state (D-8)."""
    active = await active_participants(session, room.id)
    submissions, details = await read_submissions(client, current.id, active)
    outcome = score_round(submissions)
    by_id = {member.id: member for member in active}
    added: dict[int, Submission] = {}
    totals: dict[int, int] = {}
    for result in outcome.results:
        member = by_id.get(result.participant_id)
        if member is None:
            # A participant who left after submitting keeps no place in this round's ledger.
            continue
        row = Submission(
            round_id=current.id,
            participant_id=result.participant_id,
            status=result.status,
            received_at=_received_at(submissions, result),
            target_score=result.target_score,
            top_emotions=details.get(result.participant_id, {}).get("topEmotions"),
            rank=result.rank,
            rank_points=result.rank_points,
        )
        session.add(row)
        added[result.participant_id] = row
        if not outcome.voided:
            updated = accumulate(
                ParticipantTotals(
                    participant_id=member.id,
                    joined_at=member.joined_at,
                    total_points=member.total_points,
                    best_round_score=member.best_round_score,
                ),
                result,
            )
            member.total_points = updated.total_points
            member.best_round_score = updated.best_round_score
        totals[result.participant_id] = member.total_points
    await session.flush()
    rows = {participant_id: row.id for participant_id, row in added.items()}

    finalized_at = clock.now_utc()
    current.finalized_at = finalized_at
    viewers = tuple(
        result.participant_id
        for result in outcome.results
        if result.status != SubmissionStatus.MISSED
    )
    missed = tuple(
        result.participant_id
        for result in outcome.results
        if result.status == SubmissionStatus.MISSED
    )
    if outcome.voided:
        # D-5: no points, no viewing, straight on to the next round.
        current.status = RoundStatus.VOIDED
        room.consecutive_voided += 1
        viewing_ends_at_ms = None
    else:
        current.status = RoundStatus.FINALIZED
        room.consecutive_voided = 0
        if viewers:
            current.viewing_ends_at = finalized_at + timedelta(
                seconds=viewing_seconds(len(viewers))
            )
            viewing_ends_at_ms = clock.to_epoch_ms(current.viewing_ends_at)
        else:
            # Nobody may look at an empty round, so the viewing stage has no audience (G-07).
            viewing_ends_at_ms = None
    room.last_active_at = finalized_at
    return Finalized(outcome, rows, totals, viewers, missed, viewing_ends_at_ms)


def _received_at(submissions: tuple[RoundSubmission, ...], result: ScoredSubmission) -> Any:
    for submission in submissions:
        if submission.participant_id == result.participant_id and submission.received_at_ms:
            return clock.from_epoch_ms(submission.received_at_ms)
    return None


def finalized_view(current: Round, finalized: Finalized) -> dict[str, Any]:
    return {
        "roundId": str(current.id),
        "results": [
            {
                "participantId": str(result.participant_id),
                "rank": result.rank,
                "targetScore": (
                    float(result.target_score) if result.target_score is not None else None
                ),
                "rankPoints": result.rank_points,
                "totalPoints": finalized.totals.get(result.participant_id, 0),
                "status": result.status.value,
            }
            for result in finalized.outcome.results
        ],
        "viewingEndsAtMs": finalized.viewing_ends_at_ms,
    }


async def close_round(session: AsyncSession, media: Redis, current: Round) -> None:
    if current.status == RoundStatus.FINALIZED:
        current.status = RoundStatus.CLOSED
        current.closed_at = clock.now_utc()
    await drop_round_media(session, media, current)


async def drop_round_media(session: AsyncSession, media: Redis, current: Round) -> None:
    """PV-01·02: result images die with the round, well before their 180s safety TTL."""
    participants = list(
        await session.scalars(
            select(Submission.participant_id).where(Submission.round_id == current.id)
        )
    )
    keys = [image_key(current.id, participant_id) for participant_id in participants]
    if keys:
        await cast(Awaitable[int], media.delete(*keys))


async def finish_game(session: AsyncSession, room: Room, reason: str | None) -> dict[str, Any]:
    """§10.6. The owner slot returns the moment the room is finished (RO-13, FN-01)."""
    members = list(
        await session.scalars(
            select(Participant).where(
                Participant.room_id == room.id,
                Participant.status.in_(
                    (ParticipantStatus.ACTIVE, ParticipantStatus.WAITING_NEXT_GAME)
                ),
            )
        )
    )
    reactions = await _reaction_totals(session, room.id)
    totals = tuple(
        ParticipantTotals(
            participant_id=member.id,
            joined_at=member.joined_at,
            total_points=member.total_points,
            best_round_score=member.best_round_score,
            like_count=reactions.get(member.id, (0, 0))[0],
            question_count=reactions.get(member.id, (0, 0))[1],
        )
        for member in members
        if member.status == ParticipantStatus.ACTIVE
    )
    by_id = {member.id: member for member in members}
    room.status = RoomStatus.FINISHED
    room.current_round_id = None
    room.last_active_at = clock.now_utc()
    award = most_loved(totals)
    return {
        "aborted": reason is not None,
        "reason": reason,
        "ranking": [
            {
                "rank": entry.rank,
                "participantId": str(entry.participant_id),
                "nickname": by_id[entry.participant_id].nickname,
                "colorTag": by_id[entry.participant_id].color_tag,
                "totalPoints": entry.total_points,
                "likeCount": reactions.get(entry.participant_id, (0, 0))[0],
                "questionCount": reactions.get(entry.participant_id, (0, 0))[1],
            }
            for entry in final_ranking(totals)
        ],
        "mostLoved": (
            None
            if award is None
            else {"participantId": str(award.participant_id), "likeCount": award.like_count}
        ),
    }


async def _reaction_totals(session: AsyncSession, room_id: int) -> dict[int, tuple[int, int]]:
    rows = await session.execute(
        select(Submission.participant_id, Submission.like_count, Submission.question_count)
        .join(Round, Round.id == Submission.round_id)
        .where(Round.room_id == room_id)
    )
    totals: dict[int, tuple[int, int]] = {}
    for participant_id, likes, questions in rows:
        current = totals.get(participant_id, (0, 0))
        totals[participant_id] = (current[0] + likes, current[1] + questions)
    return totals


def next_index(room: Room, current: Round) -> int | None:
    if current.index >= len(room.emotion_sequence):
        return None
    return current.index + 1


async def abort_reason(session: AsyncSession, room: Room) -> str | None:
    if room.consecutive_voided >= MAX_CONSECUTIVE_VOIDED:
        return "engine_unavailable"
    active = await session.scalar(
        select(Participant)
        .where(Participant.room_id == room.id, Participant.status == ParticipantStatus.ACTIVE)
        .limit(MIN_PLAYERS)
        .offset(MIN_PLAYERS - 1)
    )
    return None if active is not None else "not_enough_players"
