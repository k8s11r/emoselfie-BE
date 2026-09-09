from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.resources import Resources
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_lifespan_readiness_and_dependency_failure(settings, monkeypatch):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        resources = app.state.resources
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health/live")).status_code == 200
            ready = await client.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json()["inferenceBackend"] == "fake"
            assert all(ready.json()["checks"].values())
            assert ready.headers["cache-control"] == "no-store"
            monkeypatch.setattr(
                resources.media_redis, "ping", AsyncMock(side_effect=OSError("secret"))
            )
            failed = await client.get("/health/ready")
            assert failed.status_code == 503
            assert failed.json()["checks"]["mediaRedis"] is False
            assert "secret" not in failed.text
            assert (await client.get("/health/live")).status_code == 200
    assert not resources.initialized
    assert resources.classifier is None


async def test_startup_failure_closes_every_resource(settings, monkeypatch):
    resources = Resources(settings)
    close_db = AsyncMock(wraps=resources.engine.dispose)
    close_redis = AsyncMock(wraps=resources.redis.aclose)
    close_media = AsyncMock(wraps=resources.media_redis.aclose)
    monkeypatch.setattr(type(resources.engine), "dispose", close_db)
    monkeypatch.setattr(resources.redis, "aclose", close_redis)
    monkeypatch.setattr(resources.media_redis, "aclose", close_media)
    monkeypatch.setattr(resources.redis, "ping", AsyncMock(side_effect=OSError("unavailable")))
    with pytest.raises(RuntimeError, match="dependencies are unavailable"):
        await resources.start()
    close_db.assert_awaited_once()
    close_redis.assert_awaited_once()
    close_media.assert_awaited_once()
    assert not resources.initialized
