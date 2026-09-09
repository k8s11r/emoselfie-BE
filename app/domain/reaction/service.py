"""§13.2 reactions and skip votes. Counts live in Redis until the round closes (RX-10)."""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import ROUND_TTL_SEC, reaction_key, round_key
from app.db.models import Reaction, RoundSkip, Submission
from app.domain.enums import ReactionType

# RX-04: one round trip decides add or remove, so a double tap cannot double count.
TOGGLE_REACTION = """
local marker = ARGV[1]
local field = ARGV[2]
local delta = 1
if redis.call('SADD', KEYS[1], marker) == 0 then
    redis.call('SREM', KEYS[1], marker)
    delta = -1
end
local count = redis.call('HINCRBY', KEYS[2], field, delta)
if count < 0 then
    count = 0
    redis.call('HSET', KEYS[2], field, 0)
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return {delta, count}
"""
TOGGLE_SKIP = """
local delta = 1
if redis.call('SADD', KEYS[1], ARGV[1]) == 0 then
    redis.call('SREM', KEYS[1], ARGV[1])
    delta = -1
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return {delta, redis.call('SCARD', KEYS[1])}
"""


async def toggle_reaction(
    client: Redis,
    round_id: int,
    submission_id: int,
    actor_participant_id: int,
    reaction_type: ReactionType,
) -> tuple[bool, int]:
    """Returns whether the reaction is now set and the target's new count."""
    delta, count = await cast(
        Awaitable[list[int]],
        client.eval(
            TOGGLE_REACTION,
            2,
            round_key(round_id, "rx:actors"),
            reaction_key(round_id, reaction_type),
            f"{submission_id}:{actor_participant_id}:{reaction_type.value}",
            str(submission_id),
            str(ROUND_TTL_SEC),
        ),
    )
    return delta > 0, count


async def counts(client: Redis, round_id: int) -> dict[int, tuple[int, int]]:
    totals: dict[int, tuple[int, int]] = {}
    for reaction_type in ReactionType:
        raw = await cast(
            Awaitable[dict[bytes, bytes]], client.hgetall(reaction_key(round_id, reaction_type))
        )
        for key, value in raw.items():
            likes, questions = totals.get(int(key), (0, 0))
            if reaction_type == ReactionType.LIKE:
                totals[int(key)] = (int(value), questions)
            else:
                totals[int(key)] = (likes, int(value))
    return totals


async def count_for(client: Redis, round_id: int, submission_id: int) -> tuple[int, int]:
    return (await counts(client, round_id)).get(submission_id, (0, 0))


async def actors(client: Redis, round_id: int) -> list[tuple[int, int, ReactionType]]:
    members = await cast(Awaitable[set[bytes]], client.smembers(round_key(round_id, "rx:actors")))
    parsed: list[tuple[int, int, ReactionType]] = []
    for member in members:
        submission_id, actor_id, kind = member.decode().split(":")
        parsed.append((int(submission_id), int(actor_id), ReactionType(kind)))
    return parsed


async def toggle_skip(client: Redis, round_id: int, participant_id: int) -> tuple[bool, int]:
    delta, total = await cast(
        Awaitable[list[int]],
        client.eval(
            TOGGLE_SKIP,
            1,
            round_key(round_id, "skips"),
            str(participant_id),
            str(ROUND_TTL_SEC),
        ),
    )
    return delta > 0, total


async def skip_voters(client: Redis, round_id: int) -> set[int]:
    members = await cast(Awaitable[set[bytes]], client.smembers(round_key(round_id, "skips")))
    return {int(member) for member in members}


async def persist(
    session: AsyncSession, client: Redis, round_id: int
) -> dict[int, tuple[int, int]]:
    """RX-10: the closing snapshot is what reaches PostgreSQL, once, at round close."""
    totals = await counts(client, round_id)
    rows = {
        row.id: row
        for row in await session.scalars(select(Submission).where(Submission.round_id == round_id))
    }
    existing = {
        (reaction.target_submission_id, reaction.actor_participant_id, reaction.type)
        for reaction in await session.scalars(select(Reaction).where(Reaction.round_id == round_id))
    }
    for submission_id, actor_id, reaction_type in await actors(client, round_id):
        if submission_id not in rows or (submission_id, actor_id, reaction_type) in existing:
            continue
        session.add(
            Reaction(
                round_id=round_id,
                actor_participant_id=actor_id,
                target_submission_id=submission_id,
                type=reaction_type,
            )
        )
    for submission_id, (likes, questions) in totals.items():
        row = rows.get(submission_id)
        if row is not None:
            row.like_count, row.question_count = likes, questions
    for participant_id in await skip_voters(client, round_id):
        if not await session.get(RoundSkip, (round_id, participant_id)):
            session.add(RoundSkip(round_id=round_id, participant_id=participant_id))
    return totals


async def drop(client: Redis, round_id: int) -> None:
    await cast(
        Awaitable[int],
        client.delete(
            round_key(round_id, "rx:actors"),
            round_key(round_id, "skips"),
            *[reaction_key(round_id, kind) for kind in ReactionType],
        ),
    )
