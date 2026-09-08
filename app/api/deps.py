from typing import Annotated

from fastapi import Depends, Request

from app.core.ratelimit import enforce_limit
from app.core.resources import Resources
from app.db.models import User
from app.domain.user.service import ensure_user


def resources(request: Request) -> Resources:
    value: Resources = request.app.state.resources
    return value


Runtime = Annotated[Resources, Depends(resources)]


async def current_user(request: Request, runtime: Runtime) -> User:
    async with runtime.sessions.begin() as session:
        return await ensure_user(session, request.state.user_id)


CurrentUser = Annotated[User, Depends(current_user)]


async def limit_ip(request: Request, scope: str, capacity: int, window_sec: int) -> None:
    runtime = resources(request)
    # Forwarded headers are not trusted here. Configure Uvicorn's trusted proxy list in Infra.
    subject = request.client.host if request.client else "unknown"
    await enforce_limit(runtime.redis, scope, subject, capacity=capacity, window_sec=window_sec)
