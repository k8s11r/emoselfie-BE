from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.dependency_timeout_sec,
            "server_settings": {"timezone": "UTC"},
        },
        hide_parameters=True,
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # Domain mutations own `async with sessions.begin()`; repositories never commit.
    return async_sessionmaker(engine, expire_on_commit=False)
