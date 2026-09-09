import asyncio
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.core.redis import IMAGE_TTL_SEC, RedisLease, image_key

pytestmark = pytest.mark.integration


async def test_atomic_lock_and_stale_owner_release(settings):
    client = Redis.from_url(str(settings.redis_url))
    key = f"test:lock:{uuid4()}"
    try:
        attempts = await asyncio.gather(*(RedisLease.acquire(client, key, 5000) for _ in range(8)))
        owners = [owner for owner in attempts if owner is not None]
        assert len(owners) == 1
        old_owner = owners[0]
        assert 0 < await client.pttl(key) <= 5000
        await client.pexpire(key, 1)
        # Allow the real Redis lease to expire before a different worker acquires it.
        await asyncio.sleep(0.02)
        new_owner = await RedisLease.acquire(client, key, 5000)
        assert new_owner is not None
        assert await old_owner.release() is False
        assert await client.get(key) == new_owner.token.encode()
        assert await new_owner.release() is True
        assert await new_owner.release() is False
    finally:
        await client.delete(key)
        await client.aclose()


async def test_image_instance_is_separate_binary_ephemeral_cache(settings):
    coordination = Redis.from_url(str(settings.redis_url))
    media = Redis.from_url(str(settings.redis_media_url))
    key = image_key(uuid4().int, 1)
    try:
        await media.set(key, b"\xff\xd8synthetic-jpeg\xff\xd9", ex=IMAGE_TTL_SEC)
        assert await coordination.get(key) is None
        assert await media.get(key) == b"\xff\xd8synthetic-jpeg\xff\xd9"
        assert 0 < await media.ttl(key) <= 180
        assert (await media.config_get("save"))["save"] == ""
        assert (await media.config_get("appendonly"))["appendonly"] == "no"
        assert (await media.config_get("maxmemory-policy"))["maxmemory-policy"] == "allkeys-lru"
        assert (await coordination.config_get("maxmemory-policy"))[
            "maxmemory-policy"
        ] == "noeviction"
        await media.delete(key)
        assert await media.get(key) is None
    finally:
        await media.delete(key)
        await media.aclose()
        await coordination.aclose()
