import secrets
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Literal, cast

from redis.asyncio import Redis

from app.domain.enums import ReactionType

SOCKET_TTL_SEC = 7200
PRESENCE_TTL_SEC = 3600
ROUND_TTL_SEC = 1800
IMAGE_TTL_SEC = 180
ROOM_LOCK_TTL_MS = 5000
JOB_LOCK_TTL_MS = 30000
SCHEDULER_TIMERS = "sched:timers"
# A Pod refreshes its own liveness so a socket left behind by a crash can be recognised.
POD_TTL_SEC = 60
POD_REFRESH_SEC = 20


def socket_key(sid: str) -> str:
    return f"sock:{sid}"


def user_socket_key(user_id: str) -> str:
    return f"user:sock:{user_id}"


def room_key(room_id: int, kind: Literal["presence", "tempHost"]) -> str:
    return f"room:{room_id}:{kind}"


def round_key(
    round_id: int, kind: Literal["submitted", "scores", "skips", "viewers", "rx:actors"]
) -> str:
    return f"round:{round_id}:{kind}"


def reaction_key(round_id: int, reaction_type: ReactionType) -> str:
    return f"round:{round_id}:rx:{reaction_type.value}"


def capture_token_key(round_id: int, participant_id: int) -> str:
    return f"round:{round_id}:tok:{participant_id}"


def image_key(round_id: int, participant_id: int) -> str:
    return f"img:{round_id}:{participant_id}"


def room_lock_key(room_id: int) -> str:
    return f"lock:room:{room_id}"


def job_lock_key(job_id: str) -> str:
    return f"sched:lock:{job_id}"


def pod_key(pod_id: str) -> str:
    return f"pod:{pod_id}"


def rate_limit_key(scope: str, subject: str) -> str:
    return f"rl:{scope}:{subject}"


# A worker whose lease expired must never delete a newer worker's lock.
RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class RedisLease:
    client: Redis
    key: str
    token: str

    @classmethod
    async def acquire(cls, client: Redis, key: str, ttl_ms: int) -> "RedisLease | None":
        if ttl_ms <= 0:
            raise ValueError("Lease TTL must be positive")
        token = secrets.token_urlsafe(32)
        if await client.set(key, token, nx=True, px=ttl_ms):
            return cls(client, key, token)
        return None

    async def release(self) -> bool:
        # redis-py shares the command stub with its sync client; this client is async.
        result = await cast(
            Awaitable[int], self.client.eval(RELEASE_SCRIPT, 1, self.key, self.token)
        )
        return bool(result)
