from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.api.media import router as media_router
from app.api.middleware import SessionMiddleware
from app.api.rooms import router as rooms_router
from app.api.session import router as session_router
from app.api.submissions import router as submissions_router
from app.core.config import Settings
from app.core.resources import Resources
from app.realtime.server import SocketGateway


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = Resources(settings or Settings())
        app.state.resources = resources
        await resources.start()
        try:
            yield
        finally:
            await resources.close()

    app = FastAPI(title="Emoselfie Backend", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(SessionMiddleware)
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(rooms_router)
    app.include_router(submissions_router)
    app.include_router(media_router)
    app.mount("/socket.io", SocketGateway())
    return app
