import asyncio
from contextlib import AsyncExitStack

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_engine, session_factory
from app.domain.round.runner import RoundRunner
from app.domain.scheduler.service import SchedulerLoop
from app.inference.loader import load_classifier
from app.inference.protocol import ManagedEmotionClassifier
from app.realtime.server import Realtime


class Resources:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.initialized = False
        self.classifier: ManagedEmotionClassifier | None = None
        self.realtime: Realtime | None = None
        self.rounds = RoundRunner(self)
        self.scheduler: SchedulerLoop | None = None
        self.engine: AsyncEngine = create_engine(settings)
        self.sessions = session_factory(self.engine)
        self.redis = Redis.from_url(
            str(settings.redis_url),
            socket_connect_timeout=settings.dependency_timeout_sec,
            socket_timeout=settings.dependency_timeout_sec,
        )
        self.media_redis = Redis.from_url(
            str(settings.redis_media_url),
            socket_connect_timeout=settings.dependency_timeout_sec,
            socket_timeout=settings.dependency_timeout_sec,
        )
        self._cleanup = AsyncExitStack()

    async def start(self) -> None:
        self._cleanup.push_async_callback(self.engine.dispose)
        self._cleanup.push_async_callback(self.redis.aclose)
        self._cleanup.push_async_callback(self.media_redis.aclose)
        try:
            self.classifier = await load_classifier(self.settings)
            self._cleanup.push_async_callback(self.classifier.aclose)
            if not all((await self.dependency_checks()).values()):
                raise RuntimeError("Required backend dependencies are unavailable")
            self.initialized = True
            self.realtime = Realtime(self)
            # Every Pod polls; §10.4's atomic claim keeps each timer firing exactly once.
            self.scheduler = SchedulerLoop(
                self.redis, self.rounds.handle, tick_ms=self.settings.scheduler_tick_ms
            )
            self.scheduler.start()
        except BaseException:
            await self.close()
            raise

    async def dependency_checks(self) -> dict[str, bool]:
        async def database() -> bool:
            try:
                async with asyncio.timeout(self.settings.dependency_timeout_sec):
                    async with self.engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                return True
            except Exception:
                return False

        async def redis_ping(client: Redis) -> bool:
            try:
                async with asyncio.timeout(self.settings.dependency_timeout_sec):
                    return bool(await client.ping())
            except Exception:
                return False

        db, coordination, media = await asyncio.gather(
            database(), redis_ping(self.redis), redis_ping(self.media_redis)
        )
        return {"database": db, "redis": coordination, "mediaRedis": media}

    async def close(self) -> None:
        self.initialized = False
        self.classifier = None
        try:
            if self.scheduler is not None:
                await self.scheduler.stop()
                self.scheduler = None
            if self.realtime is not None:
                await self.realtime.shutdown()
                self.realtime = None
        finally:
            await self._cleanup.aclose()
