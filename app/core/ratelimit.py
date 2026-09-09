import math
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from app.core import clock
from app.core.errors import AppError
from app.core.redis import rate_limit_key
from app.core.security import private_rate_subject

BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(data[1]) or capacity
local previous = tonumber(data[2]) or now
now = math.max(previous, now)
tokens = math.min(capacity, tokens + (now - previous) * capacity / window)
local retry = 0
if tokens >= 1 then
    tokens = tokens - 1
else
    retry = math.ceil((1 - tokens) * window / capacity)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('PEXPIRE', KEYS[1], window)
return retry
"""


async def enforce_limit(
    client: Redis, scope: str, subject: str, *, capacity: int, window_sec: int
) -> None:
    key = rate_limit_key(scope, private_rate_subject(subject))
    retry_ms = await cast(
        Awaitable[int],
        client.eval(
            BUCKET_SCRIPT, 1, key, str(capacity), str(window_sec * 1000), str(clock.now_ms())
        ),
    )
    if retry_ms:
        raise AppError("RATE_LIMITED", retry_after=max(1, math.ceil(retry_ms / 1000)))
