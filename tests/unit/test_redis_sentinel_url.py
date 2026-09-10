"""REDIS_URL이 Sentinel 형식을 받는지, 클라이언트가 그에 맞게 갈라지는지."""

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.asyncio.sentinel import SentinelConnectionPool

from app.core.config import Settings, _endpoints
from app.core.resources import redis_client

SENTINEL_URL = "redis+sentinel://s1:26379,s2:26379,s3:26379/0/mymaster"

BASE = {
    "database_url": "postgresql+asyncpg://u:p@db:5432/d",
    "redis_media_url": "redis://redis-media:6379/0",
    "cookie_secret": "a" * 32,
    "capture_token_secret": "b" * 32,
    "media_token_secret": "c" * 32,
    "app_env": "development",
}


def settings(redis_url: str) -> Settings:
    return Settings(redis_url=redis_url, **BASE)


@pytest.mark.parametrize("url", ["redis://redis:6379/0", "rediss://redis:6379/0", SENTINEL_URL])
def test_accepts_supported_schemes(url: str) -> None:
    assert settings(url).redis_url == url


@pytest.mark.parametrize("url", ["http://redis:6379", "redis-sentinel://s1:26379"])
def test_rejects_other_schemes(url: str) -> None:
    with pytest.raises(ValidationError):
        settings(url)


def test_endpoints_parses_sentinel_list_and_default_port() -> None:
    assert _endpoints(SENTINEL_URL) == {("s1", 26379), ("s2", 26379), ("s3", 26379)}
    assert _endpoints("redis://redis/0") == {("redis", 6379)}
    assert _endpoints("redis://:pw@redis:6379/0") == {("redis", 6379)}


def test_rejects_shared_instance_even_without_explicit_port() -> None:
    # 예전 검사는 (host, port) 튜플 비교라 포트를 생략하면 놓쳤다.
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "redis_url": "redis://redis-media/0"})


def test_client_branches_on_scheme() -> None:
    assert isinstance(redis_client("redis://redis:6379/0"), Redis)

    sentinel_backed = redis_client(SENTINEL_URL)
    # master_for 가 돌려주는 클라이언트는 연결할 때마다 Sentinel에게 master를
    # 물어보는 커넥션 풀을 쓴다. 주소가 고정되지 않는다는 것이 요점이다.
    assert isinstance(sentinel_backed.connection_pool, SentinelConnectionPool)
    assert sentinel_backed.connection_pool.service_name == "mymaster"
