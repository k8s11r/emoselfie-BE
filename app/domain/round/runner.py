"""Drives one round through §10 by reacting to scheduler jobs. Events are emitted after commit."""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.db.models import Participant, Room, Round
from app.domain.enums import RoomStatus, RoundStatus
from app.domain.game.service import active_participants, open_round
from app.domain.round import service
from app.domain.scheduler.service import Job, cancel, schedule
from app.realtime.emitter import PersonalEvent, RoomEvent, ViewerEvent

if TYPE_CHECKING:
    from app.core.resources import Resources

Delivery = tuple[Participant, dict[str, Any]]


class RoundRunner:
    def __init__(self, resources: "Resources") -> None:
        self.runtime = resources

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
            pending = await service.pending_inference(self.runtime.redis, current.id)
            if pending:
                await schedule(
                    self.runtime.redis,
                    Job("round_scoring_guard", current.id),
                    clock.now_ms() + service.SCORING_GUARD_SEC * 1000,
                )
        # D-4 stage one: a missed player learns the phase, never another player's score (RS-14).
        await self._send_personal("round:missed", missed_events)
        if not pending:
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
        async with self.runtime.sessions.begin() as session:
            loaded = await self._load(session, round_id)
            if loaded is None:
                return
            room, current = loaded
            if current.status not in (RoundStatus.FINALIZED, RoundStatus.VOIDED):
                return
            await service.close_round(session, self.runtime.media_redis, current)
            room_id = room.id
            reason = await service.abort_reason(session, room)
            following = service.next_index(room, current)
            if reason is not None or following is None:
                finished = await service.finish_game(session, room, reason)
            else:
                upcoming = await open_round(session, room, following)
                await self.schedule_deadline(upcoming)
                next_round = True
        await cancel(self.runtime.redis, Job("round_viewing_end", round_id))
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

    async def _send_room(self, event: RoomEvent, room_id: int, payload: dict[str, Any]) -> None:
        if self.runtime.realtime is not None:
            await self.runtime.realtime.emitter.room(event, room_id, payload)

    async def _send_viewers(
        self, event: ViewerEvent, round_id: int, payload: dict[str, Any]
    ) -> None:
        if self.runtime.realtime is not None:
            await self.runtime.realtime.emitter.viewers(event, round_id, payload)
