"""Drives one round through §10 by reacting to scheduler jobs. Events are emitted after commit."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import AppError
from app.core.redis import IMAGE_TTL_SEC, image_key
from app.core.security import media_token
from app.db.models import Participant, Room, Round, Submission
from app.domain.enums import EmotionLabel, RoomStatus, RoundStatus, SubmissionStatus
from app.domain.game.service import active_participants, open_round
from app.domain.reaction import service as reactions
from app.domain.round import service
from app.domain.scheduler.service import Job, cancel, schedule
from app.domain.scoring.service import target_score
from app.inference.protocol import EmotionResult, InferenceError
from app.media.images import decode_jpeg, encode_result_jpeg
from app.realtime.emitter import PersonalEvent, PlayerEvent, RoomEvent, ViewerEvent

if TYPE_CHECKING:
    from app.core.resources import Resources

Delivery = tuple[Participant, dict[str, Any]]


class RoundRunner:
    def __init__(self, resources: "Resources") -> None:
        self.runtime = resources
        self._tasks: set[asyncio.Task[None]] = set()

    async def shutdown(self) -> None:
        """Graceful shutdown waits for in-flight scoring so a result is never half written."""
        pending = list(self._tasks)
        if not pending:
            return
        done, unfinished = await asyncio.wait(pending, timeout=5)
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.wait(unfinished, timeout=5)

    async def handle(self, job: Job) -> None:
        if job.kind == "round_deadline":
            await self.close_submissions(job.target_id)
        elif job.kind == "round_scoring_guard":
            await self.force_finalize(job.target_id)
        elif job.kind == "round_viewing_end":
            await self.advance(job.target_id)
        # participant_left, host_delegate and room_expire arrive with BE-050·051·027.

    async def schedule_deadline(self, current: Round) -> None:
        await schedule(
            self.runtime.redis,
            Job("round_deadline", current.id),
            clock.to_epoch_ms(current.deadline_at),
        )

    async def _load(self, session: AsyncSession, round_id: int) -> tuple[Room, Round] | None:
        """The room row lock serialises every transition for this room (§10.2)."""
        current = await session.get(Round, round_id)
        if current is None:
            return None
        room = await session.get(Room, current.room_id, with_for_update=True)
        if room is None or room.status != RoomStatus.PLAYING:
            return None
        await session.refresh(current)
        return room, current

    async def close_submissions(self, round_id: int) -> None:
        """§10.2. The deadline and a full house may both arrive; only one may transition."""
        pending = 0
        missed_events: list[Delivery] = []
        async with self.runtime.sessions.begin() as session:
            loaded = await self._load(session, round_id)
            if loaded is None:
                return
            room, current = loaded
            if not await service.close_submissions(session, current):
                return
            active = await active_participants(session, room.id)
            submissions, _ = await service.read_submissions(self.runtime.redis, current.id, active)
            missed = {
                submission.participant_id
                for submission in submissions
                if submission.received_at_ms is None
            }
            missed_events = [
                (
                    member,
                    {
                        "roundId": str(current.id),
                        "index": current.index,
                        "roundCount": room.round_count,
                        "phase": "scoring",
                    },
                )
                for member in active
                if member.id in missed
            ]
        # The pending count is read only after `scoring` is committed. A result recorded
        # before this read is counted here; one recorded after sees `scoring` and finalizes
        # itself, so neither side can leave the round waiting for the guard.
        pending = await service.pending_inference(self.runtime.redis, round_id)
        if pending:
            await schedule(
                self.runtime.redis,
                Job("round_scoring_guard", round_id),
                clock.now_ms() + service.SCORING_GUARD_SEC * 1000,
            )
        # D-4 stage one: a missed player learns the phase, never another player's score (RS-14).
        await self._send_personal("round:missed", missed_events)
        if not pending:
            await self.finalize(round_id)

    async def dispatch(
        self, round_id: int, room_id: int, participant_id: int, submission_id: int, image: bytes
    ) -> None:
        """§8.3 ⑦~⑨. The response never waits for the engine."""
        submitted = await service.submitted_count(self.runtime.redis, round_id)
        async with self.runtime.sessions() as session:
            total = len(await active_participants(session, room_id))
        await self._send_players(
            room_id,
            "submission:status",
            {"roundId": str(round_id), "submitted": submitted, "total": total},
        )
        await self.send_backlog(round_id, room_id, participant_id)
        task = asyncio.create_task(
            self._score(round_id, room_id, participant_id, submission_id, image)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if submitted >= total:
            # RD-07: a full house closes the round without waiting for the deadline.
            await self.close_submissions(round_id)

    async def _score(
        self, round_id: int, room_id: int, participant_id: int, submission_id: int, image: bytes
    ) -> None:
        classifier = self.runtime.classifier
        payload: dict[str, Any] = {"status": SubmissionStatus.FAILED.value}
        try:
            await self._store_image(round_id, participant_id, image)
            if classifier is None:
                raise InferenceError("Inference is unavailable")
            result = await classifier.classify(image)
            payload = self._result_payload(result, await self._target(round_id))
        except (InferenceError, TimeoutError, AppError):
            payload = {"status": SubmissionStatus.FAILED.value}
        except asyncio.CancelledError:
            payload = {"status": SubmissionStatus.FAILED.value}
            raise
        finally:
            del image
            await service.record_score(self.runtime.redis, round_id, participant_id, payload)
            await self._announce_score(round_id, room_id, participant_id, submission_id, payload)
            await self._finalize_if_resolved(round_id)

    async def _target(self, round_id: int) -> EmotionLabel:
        async with self.runtime.sessions() as session:
            current = await session.get(Round, round_id)
            if current is None:
                raise InferenceError("The round disappeared before scoring")
            return current.target_emotion

    def _result_payload(self, result: EmotionResult, target: EmotionLabel) -> dict[str, Any]:
        if not result.face_detected or result.probabilities is None:
            return {"status": SubmissionStatus.NO_FACE.value, "targetScore": 0.0}  # SC-04
        ranked = sorted(result.probabilities.items(), key=lambda item: -item[1])
        return {
            "status": SubmissionStatus.SUBMITTED.value,
            "targetScore": float(target_score(result.probabilities[target])),
            "topEmotions": [
                {"label": label.value, "score": float(target_score(value))}
                for label, value in ranked[:3]
            ],
        }

    async def _store_image(self, round_id: int, participant_id: int, image: bytes) -> None:
        """D-1: only the re-encoded result frame is cached, and only until the round ends."""
        with decode_jpeg(
            image,
            max_bytes=self.runtime.settings.max_upload_bytes,
            max_pixels=self.runtime.settings.max_image_pixels,
        ) as decoded:
            result = encode_result_jpeg(decoded)
        await self.runtime.media_redis.set(
            image_key(round_id, participant_id), result, ex=IMAGE_TTL_SEC
        )

    def _scored_card(
        self,
        current: Round,
        owner: Participant,
        submission_id: int,
        payload: dict[str, Any],
        rank: int | None,
        scored: int,
        total: int,
    ) -> Callable[[Participant], dict[str, Any]]:
        expires_at_ms = service.media_expiry_ms(current)
        secret = self.runtime.settings.media_token_secret.get_secret_value()

        def per_viewer(viewer: Participant) -> dict[str, Any]:
            # RX-08: a photo exists even for no_face and failed, so every viewer gets a token.
            return {
                "roundId": str(current.id),
                "submissionId": str(submission_id),
                "participantId": str(owner.id),
                "nickname": owner.nickname,
                "colorTag": owner.color_tag,
                "status": payload["status"],
                "targetScore": payload.get("targetScore"),
                "topEmotions": payload.get("topEmotions"),
                "currentRank": rank,
                "mediaToken": media_token(
                    current.id, submission_id, viewer.id, expires_at_ms, secret
                ),
                "scoredCount": scored,
                "scoredTotal": total,
            }

        return per_viewer

    async def _announce_score(
        self,
        round_id: int,
        room_id: int,
        participant_id: int,
        submission_id: int,
        payload: dict[str, Any],
    ) -> None:
        async with self.runtime.sessions() as session:
            owner = await session.get(Participant, participant_id)
            current = await session.get(Round, round_id)
            if owner is None or current is None:
                return
            active = len(await active_participants(session, room_id))
        received = await service.read_received(self.runtime.redis, round_id)
        scores = await service.read_scores(self.runtime.redis, round_id)
        rank = service.display_ranks(received, scores).get(participant_id)
        card = self._scored_card(current, owner, submission_id, payload, rank, len(scores), active)
        await self._send_viewers("submission:scored", round_id, card)

    async def send_backlog(self, round_id: int, room_id: int, participant_id: int) -> None:
        """RS-02·04: a viewer joins mid-round, so the results settled before them are replayed.

        `submission:scored` only reaches the viewers of its own moment, so without this a late
        submitter would never learn about the cards that were scored while they were shooting.
        """
        scores = await service.read_scores(self.runtime.redis, round_id)
        earlier = {owner: payload for owner, payload in scores.items() if owner != participant_id}
        if not earlier:
            return
        received = await service.read_received(self.runtime.redis, round_id)
        ranks = service.display_ranks(received, scores)
        async with self.runtime.sessions() as session:
            current = await session.get(Round, round_id)
            if current is None:
                return
            rows = {
                row.participant_id: row.id
                for row in await session.scalars(
                    select(Submission).where(Submission.round_id == round_id)
                )
            }
            owners = {member.id: member for member in await active_participants(session, room_id)}
            active = len(owners)
        # Replay in arrival order so the receiving rail builds the same way it did live.
        for owner_id in sorted(earlier, key=lambda value: (received.get(value, 0), value)):
            owner = owners.get(owner_id)
            submission_id = rows.get(owner_id)
            if owner is None or submission_id is None:
                continue
            card = self._scored_card(
                current,
                owner,
                submission_id,
                earlier[owner_id],
                ranks.get(owner_id),
                len(scores),
                active,
            )
            await self._send_viewers("submission:scored", round_id, card, only={participant_id})

    async def _finalize_if_resolved(self, round_id: int) -> None:
        async with self.runtime.sessions() as session:
            current = await session.get(Round, round_id)
        if current is None or current.status != RoundStatus.SCORING:
            return
        if await service.pending_inference(self.runtime.redis, round_id) <= 0:
            await self.finalize(round_id)

    async def force_finalize(self, round_id: int) -> None:
        """§10.3 safety net at deadline + 8s: unresolved inference becomes a failure."""
        await service.force_pending_failed(self.runtime.redis, round_id)
        await self.finalize(round_id)

    async def finalize(self, round_id: int) -> None:
        async with self.runtime.sessions.begin() as session:
            loaded = await self._load(session, round_id)
            if loaded is None:
                return
            room, current = loaded
            if current.status != RoundStatus.SCORING:
                return
            finalized = await service.finalize_round(session, self.runtime.redis, room, current)
            payload = service.finalized_view(current, finalized)
            voided = finalized.outcome.voided
            room_id, index = room.id, current.index
            viewing_ends_at_ms = finalized.viewing_ends_at_ms
            members = {member.id: member for member in await active_participants(session, room.id)}
            missed_events = [
                (
                    members[participant_id],
                    {
                        "roundId": str(round_id),
                        "phase": "viewing",
                        "nextRoundAtMs": viewing_ends_at_ms,
                    },
                )
                for participant_id in finalized.missed
                if participant_id in members
            ]
        await cancel(self.runtime.redis, Job("round_scoring_guard", round_id))
        if voided:
            # D-5 goes to everyone: it carries no result, so RS-12 is not at stake.
            await self._send_room(
                "round:voided",
                room_id,
                {
                    "roundId": str(round_id),
                    "index": index,
                    "reason": "engine_unavailable",
                    "nextRoundAtMs": clock.now_ms(),
                },
            )
        else:
            await self._send_viewers("round:finalized", round_id, payload)
            # D-4 stage two carries the remaining time and nothing else (RS-14).
            await self._send_personal("round:missedUpdate", missed_events)
        if viewing_ends_at_ms is None:
            # A voided round and a round nobody may view both skip the viewing stage.
            await self.advance(round_id)
        else:
            await schedule(
                self.runtime.redis, Job("round_viewing_end", round_id), viewing_ends_at_ms
            )

    async def advance(self, round_id: int) -> None:
        """§10.5·10.6. Close the round, then open the next one or finish the game."""
        finished: dict[str, Any] | None = None
        next_round = False
        next_round_at_ms = 0
        async with self.runtime.sessions.begin() as session:
            loaded = await self._load(session, round_id)
            if loaded is None:
                return
            room, current = loaded
            if current.status not in (RoundStatus.FINALIZED, RoundStatus.VOIDED):
                return
            voided = current.status == RoundStatus.VOIDED
            reaction_totals = await service.close_round(
                session, self.runtime.redis, self.runtime.media_redis, current
            )
            results, totals, rows = await service.settled_results(session, current.id)
            room_id = room.id
            reason = await service.abort_reason(session, room)
            following = service.next_index(room, current)
            if reason is not None or following is None:
                finished = await service.finish_game(session, room, reason)
            else:
                upcoming = await open_round(session, room, following)
                await self.schedule_deadline(upcoming)
                next_round = True
                next_round_at_ms = clock.to_epoch_ms(upcoming.revealed_at)
            closed_payload = (
                None
                if voided
                else service.closed_view(
                    current,
                    totals,
                    reaction_totals,
                    rows,
                    results,
                    next_round_at_ms if next_round else clock.now_ms(),
                )
            )
        await cancel(self.runtime.redis, Job("round_viewing_end", round_id))
        if closed_payload is not None:
            await self._send_viewers("round:closed", round_id, closed_payload)
        await reactions.drop(self.runtime.redis, round_id)
        if finished is not None:
            await self._send_room("game:finished", room_id, finished)
        elif next_round and self.runtime.realtime is not None:
            await self.runtime.realtime.announce_round(room_id)

    async def _send_personal(self, event: PersonalEvent, targets: list[Delivery]) -> None:
        realtime = self.runtime.realtime
        if realtime is None:
            return
        for member, payload in targets:
            sid = await realtime.current_sid(member)
            if sid is not None:
                await realtime.emitter.personal(event, sid, payload)

    async def _send_players(
        self, room_id: int, event: PlayerEvent, payload: dict[str, Any]
    ) -> None:
        if self.runtime.realtime is not None:
            await self.runtime.realtime.send_players(room_id, event, payload)

    async def _send_room(self, event: RoomEvent, room_id: int, payload: dict[str, Any]) -> None:
        if self.runtime.realtime is not None:
            await self.runtime.realtime.emitter.room(event, room_id, payload)

    async def _send_viewers(
        self,
        event: ViewerEvent,
        round_id: int,
        payload: dict[str, Any] | Callable[[Participant], dict[str, Any]],
        only: set[int] | None = None,
    ) -> None:
        if self.runtime.realtime is not None:
            await self.runtime.realtime.send_viewers(round_id, event, payload, only=only)
