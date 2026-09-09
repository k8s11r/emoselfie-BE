from fastapi import APIRouter

from app.api.deps import CurrentUser, Runtime
from app.api.schemas import MeResponse, NicknameInput
from app.db.models import User
from app.domain.user.service import active_room, normalize_nickname

router = APIRouter(prefix="/api/me", tags=["session"])


@router.get("")
async def me(user: CurrentUser, runtime: Runtime) -> MeResponse:
    async with runtime.sessions() as session:
        room = await active_room(session, user.uuid)
    return MeResponse(
        nickname=user.nickname,
        has_active_room=room is not None,
        active_room_slug=room.invite_slug if room else None,
    )


@router.patch("")
async def nickname(body: NicknameInput, user: CurrentUser, runtime: Runtime) -> dict[str, str]:
    normalized = normalize_nickname(body.nickname)
    async with runtime.sessions.begin() as session:
        stored = await session.get(User, user.uuid, with_for_update=True)
        assert stored is not None
        stored.nickname = normalized
    return {"nickname": normalized}
