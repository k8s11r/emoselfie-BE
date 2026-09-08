import asyncio

import pytest
from redis.asyncio import Redis

from app.core import clock
from app.core.redis import SCHEDULER_TIMERS, job_lock_key
from app.domain.scheduler.service import (
    Job,
    SchedulerLoop,
    cancel,
    claim_due,
    due_at,
    schedule,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(settings):
    client = Redis.from_url(str(settings.redis_url))
    jobs = [
        Job("round_deadline", 9_100_001),
        Job("round_scoring_guard", 9_100_001),
        Job("round_viewing_end", 9_100_001),
        Job("round_deadline", 9_100_002),
        Job("participant_left", 9_100_003),
        Job("room_expire", 9_100_004),
    ]
    try:
        yield client, jobs
    finally:
        # Only this test's members and locks; a developer's timers stay untouched.
        await client.zrem(SCHEDULER_TIMERS, *[job.member for job in jobs])
        await client.delete(*[job_lock_key(job.lock_id) for job in jobs])
        await client.aclose()


def test_job_member_round_trips_and_matches_the_documented_shape():
    job = Job("round_deadline", 87)

    assert job.member == '{"job":"round_deadline","roundId":"87"}'
    assert Job.parse(job.member) == job
    assert Job.parse(job.member.encode()) == job
    assert Job.parse(Job("participant_left", 44).member) == Job("participant_left", 44)
    assert Job.parse(Job("room_expire", 12).member) == Job("room_expire", 12)


@pytest.mark.parametrize(
    "member", ["", "{}", "not json", '{"job":"unknown","roundId":"1"}', '{"job":"room_expire"}']
)
def test_unparseable_members_are_ignored(member):
    assert Job.parse(member) is None


async def test_only_one_pod_claims_a_due_job(redis):
    client, jobs = redis
    deadline = jobs[0]
    await schedule(client, deadline, clock.now_ms() - 1)

    claims = await asyncio.gather(*(claim_due(client, clock.now_ms()) for _ in range(4)))

    assert sum(deadline in claimed for claimed in claims) == 1
    assert await due_at(client, deadline) is None


async def test_future_jobs_wait_and_cancellation_removes_them(redis):
    client, jobs = redis
    later, soon = jobs[1], jobs[2]
    fire_at = clock.now_ms() + 60_000
    await schedule(client, later, fire_at)
    await schedule(client, soon, clock.now_ms() - 5)

    assert await due_at(client, later) == fire_at
    assert await claim_due(client, clock.now_ms()) == [soon]

    assert await cancel(client, later) is True
    assert await cancel(client, later) is False
    assert await claim_due(client, fire_at + 1) == []


async def test_rescheduling_moves_a_single_member(redis):
    client, jobs = redis
    expire = jobs[5]
    await schedule(client, expire, clock.now_ms() + 30_000)
    await schedule(client, expire, clock.now_ms() + 90_000)

    assert await client.zcount(SCHEDULER_TIMERS, "-inf", "+inf") >= 1
    assert await claim_due(client, clock.now_ms() + 60_000) == []
    assert await claim_due(client, clock.now_ms() + 120_000) == [expire]


async def test_claim_batch_is_bounded(redis):
    client, jobs = redis
    past = clock.now_ms() - 1
    for job in jobs:
        await schedule(client, job, past)

    first = await claim_due(client, clock.now_ms(), limit=2)
    rest = await claim_due(client, clock.now_ms())

    assert len(first) == 2
    assert len(first) + len(rest) == len(jobs)
    assert set(first).isdisjoint(rest)


async def test_two_loops_run_each_job_once_and_survive_a_failing_handler(redis):
    client, jobs = redis
    handled: list[Job] = []
    complete = asyncio.Event()

    async def handler(job: Job) -> None:
        if job.kind == "participant_left":
            raise RuntimeError("handler failure must not stop the loop")
        handled.append(job)
        if len(handled) >= len(jobs) - 1:
            complete.set()

    loops = [SchedulerLoop(client, handler, tick_ms=25) for _ in range(2)]
    for job in jobs:
        await schedule(client, job, clock.now_ms() - 1)
    for loop in loops:
        loop.start()
    try:
        async with asyncio.timeout(5):
            await complete.wait()
        # Give a second claim a chance to double-fire before asserting each job ran once.
        await asyncio.sleep(0.2)
    finally:
        for loop in loops:
            await loop.stop()

    assert sorted(handled, key=lambda job: (job.kind, job.target_id)) == sorted(
        (job for job in jobs if job.kind != "participant_left"),
        key=lambda job: (job.kind, job.target_id),
    )
    # A successful job keeps its lock as the duplicate guard; a failed one frees it.
    assert await client.get(job_lock_key(jobs[0].lock_id)) is not None
    assert await client.get(job_lock_key(Job("participant_left", 9_100_003).lock_id)) is None


async def test_a_stopped_loop_stops_claiming(redis):
    client, jobs = redis
    handled: list[Job] = []
    loop = SchedulerLoop(client, lambda job: _record(handled, job), tick_ms=25)
    loop.start()
    await loop.stop()

    await schedule(client, jobs[3], clock.now_ms() - 1)
    await asyncio.sleep(0.2)

    assert handled == []
    assert await due_at(client, jobs[3]) is not None


async def _record(sink: list[Job], job: Job) -> None:
    sink.append(job)
