"""§10.4 distributed timers. ZSet polling with an atomic claim, never keyspace notifications."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast

from redis.asyncio import Redis

from app.core import clock
from app.core.redis import JOB_LOCK_TTL_MS, SCHEDULER_TIMERS, RedisLease, job_lock_key

JobKind = Literal[
    "round_deadline",
    "round_scoring_guard",
    "round_viewing_end",
    "participant_left",
    "host_delegate",
    "room_expire",
]
TARGET_FIELD: dict[JobKind, str] = {
    "round_deadline": "roundId",
    "round_scoring_guard": "roundId",
    "round_viewing_end": "roundId",
    "participant_left": "participantId",
    "host_delegate": "participantId",
    "room_expire": "roomId",
}
CLAIM_BATCH = 10

# ZREM decides ownership: only the Pod whose removal returned 1 may run the job.
CLAIM_DUE = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
local claimed = {}
for _, member in ipairs(due) do
    if redis.call('ZREM', KEYS[1], member) == 1 then
        claimed[#claimed + 1] = member
    end
end
return claimed
"""


@dataclass(frozen=True, slots=True)
class Job:
    kind: JobKind
    target_id: int

    @property
    def member(self) -> str:
        """The ZSet member is the identity used to cancel, so its encoding must be stable."""
        return json.dumps(
            {"job": self.kind, TARGET_FIELD[self.kind]: str(self.target_id)},
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def lock_id(self) -> str:
        return f"{self.kind}:{self.target_id}"

    @classmethod
    def parse(cls, member: str | bytes) -> "Job | None":
        try:
            payload = json.loads(member)
            kind = payload["job"]
            return cls(kind, int(payload[TARGET_FIELD[kind]]))
        except (ValueError, TypeError, KeyError):
            return None


async def schedule(client: Redis, job: Job, fire_at_ms: int) -> None:
    await client.zadd(SCHEDULER_TIMERS, {job.member: fire_at_ms})


async def cancel(client: Redis, job: Job) -> bool:
    return bool(await client.zrem(SCHEDULER_TIMERS, job.member))


async def due_at(client: Redis, job: Job) -> int | None:
    score = await client.zscore(SCHEDULER_TIMERS, job.member)
    return None if score is None else int(score)


async def claim_due(client: Redis, now_ms: int, limit: int = CLAIM_BATCH) -> list[Job]:
    members = await cast(
        Awaitable[list[bytes]],
        client.eval(CLAIM_DUE, 1, SCHEDULER_TIMERS, str(now_ms), str(limit)),
    )
    jobs = [Job.parse(member) for member in members]
    return [job for job in jobs if job is not None]


class SchedulerLoop:
    """One loop per Pod. Every Pod polls; the atomic claim keeps each firing single."""

    def __init__(
        self,
        client: Redis,
        handler: Callable[[Job], Awaitable[None]],
        *,
        tick_ms: int = 250,
    ) -> None:
        self.client = client
        self.handler = handler
        self.tick_sec = tick_ms / 1000
        self.task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        handled = 0
        for job in await claim_due(self.client, clock.now_ms()):
            # ZREM already granted ownership; the lease is the safety net against a re-added twin.
            # It is deliberately left to expire so a duplicate cannot run inside its window.
            lease = await RedisLease.acquire(
                self.client, job_lock_key(job.lock_id), JOB_LOCK_TTL_MS
            )
            if lease is None:
                continue
            try:
                await self.handler(job)
            except asyncio.CancelledError:
                await lease.release()
                raise
            except Exception:
                # The claim consumed the timer, so a failed job is lost until the G-09
                # reconciliation lands (BE-DEC-09, BE-057). Free the lock for that recovery
                # and keep the rest of this batch running.
                await lease.release()
                continue
            handled += 1
        return handled

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed tick must never stop the timer loop for the whole Pod.
                pass
            await asyncio.sleep(self.tick_sec)

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        finally:
            self.task = None
