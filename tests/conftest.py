import pytest

from app.core.config import Settings


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False)
    parser.addoption("--run-model", action="store_true", default=False)


def pytest_collection_modifyitems(config, items):
    gates = [
        (
            "integration",
            "--run-integration",
            "Use --run-integration with local PostgreSQL and Redis",
        ),
        ("model", "--run-model", "Use --run-model with the inference extra and prepared artifacts"),
    ]
    for marker, option, reason in gates:
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        app_env="test",
        inference_backend="fake",
        database_url="postgresql+asyncpg://emoselfie:local-development@127.0.0.1:55432/emoselfie",
        redis_url="redis://127.0.0.1:56379/0",
        redis_media_url="redis://127.0.0.1:56380/0",
        cookie_secret="c" * 48,
        capture_token_secret="t" * 48,
        media_token_secret="m" * 48,
        cookie_secret_previous=None,
    )


@pytest.fixture
def fixed_clock(monkeypatch):
    from app.core import clock

    instant = 1_788_800_000_123
    monkeypatch.setattr(clock, "now_ms", lambda: instant)
    return instant
