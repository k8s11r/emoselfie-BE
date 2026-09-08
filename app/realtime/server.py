import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs
from uuid import UUID

import socketio
from sqlalchemy import select, update
from starlette.types import Receive, Scope, Send

from app.core import clock
from app.core.errors import AppError
from app.core.ratelimit import enforce_limit
from app.core.redis import (
    PRESENCE_TTL_SEC,
    SOCKET_TTL_SEC,
    room_key,
    round_key,
    socket_key,
    user_socket_key,
)
from app.core.security import COOKIE_NAME, verify_cookie
from app.db.models import Participant, Room, Round, Submission, User
from app.domain.enums import ConnectionStatus, ReactionType, RoomStatus, RoundStatus
from app.domain.game import service as game
from app.domain.reaction import service as reactions
from app.domain.room import service
from app.domain.round import service as round_service
from app.realtime.emitter import Emitter, PlayerEvent, ViewerEvent

if TYPE_CHECKING:
    from app.core.resources import Resources

CLAIM_SOCKET = """
local old = redis.call('GET', KEYS[1])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[5])
redis.call('HSET', KEYS[2], 'userId', ARGV[2], 'participantId', ARGV[3], 'roomId', ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[5])
return old
"""
RELEASE_SOCKET = """
redis.call('DEL', KEYS[2])
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""
REFRESH_SOCKET = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
redis.call('HSET', KEYS[3], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[3], ARGV[5])
return 1
"""


class SocketGateway:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        realtime = scope["app"].state.resources.realtime
        await realtime.asgi(scope, receive, send)


class Realtime:
    def __init__(self, resources: "Resources") -> None:
        self.runtime = resources
        self.manager = socketio.AsyncRedisManager(str(resources.settings.redis_url))
        self.server = socketio.AsyncServer(
            async_mode="asgi",
            client_manager=self.manager,
            logger=False,
            engineio_logger=False,
            max_http_buffer_size=16384,
            ping_interval=25,
            ping_timeout=20,
        )
        self.asgi = socketio.ASGIApp(self.server, socketio_path="")
        self.emitter = Emitter(self.server)
        self.server.on("connect", self.connect)
        self.server.on("disconnect", self.disconnect)
        self.server.on("presence:ping", self.ping)
        self.server.on("reaction:sent", self.react)
        self.server.on("round:skip", self.skip)

    async def connect(self, sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
        old_room_id: int | None = None
        old_participant_id = 0
        try:
            cookies = SimpleCookie()
            cookies.load(environ.get("HTTP_COOKIE", ""))
            cookie = cookies.get(COOKIE_NAME)
            identity = verify_cookie(cookie.value if cookie else None, self.runtime.settings)
            if identity is None:
                raise AppError("NOT_A_PARTICIPANT")
            user_id = identity[0]
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=10)
            slug = query.get("slug", [""])[0]
            async with self.runtime.sessions.begin() as session:
                # User first, then room: all connections for one UUID share this order.
                user = await session.get(User, user_id, with_for_update=True)
                if user is None:
                    raise AppError("NOT_A_PARTICIPANT")
                room = await service.get_room(session, slug, lock=True)
                service.require_open(room)
                participant = await service.current_participant(session, room.id, user_id)
                if room.status != RoomStatus.WAITING:
                    raise AppError("SERVICE_UNAVAILABLE")
                old_sid = await self.runtime.redis.eval(
                    CLAIM_SOCKET,
                    2,
                    user_socket_key(str(user_id)),
                    socket_key(sid),
                    sid,
                    str(user_id),
                    str(participant.id),
                    str(room.id),
                    str(SOCKET_TTL_SEC),
                )
                if old_sid:
                    old_context = await self.runtime.redis.hgetall(socket_key(old_sid.decode()))
                    if old_context and int(old_context[b"participantId"]) != participant.id:
                        old_room_id = int(old_context[b"roomId"])
                        old_participant_id = int(old_context[b"participantId"])
                        await session.execute(
                            update(Participant)
                            .where(Participant.id == int(old_context[b"participantId"]))
                            .values(
                                connection_status=ConnectionStatus.DISCONNECTED,
                                disconnected_at=clock.now_utc(),
                            )
                        )
                participant.connection_status = ConnectionStatus.CONNECTED
                participant.disconnected_at = None
                participant_id = participant.id
                room.last_active_at = clock.now_utc()
                await self.server.enter_room(sid, f"r:{room.id}")
                await self.server.enter_room(sid, f"r:{room.id}:players")
                room_id = room.id
            if old_sid:
                old = old_sid.decode()
                await self.emitter.personal("session:superseded", old, {})
                await self.server.disconnect(old)
            # The socket arrived inside the grace window, so the slot is no longer at risk.
            await self.runtime.rounds.keep_slot(participant_id)
            if old_room_id is not None:
                await self.runtime.rounds.hold_slot(old_participant_id)
                await self.refresh_lobby(old_room_id)
            await self.refresh_lobby(room_id)
            return True
        except AppError as exc:
            with suppress(Exception):
                await self.disconnect(sid)
            raise socketio.exceptions.ConnectionRefusedError(exc.envelope()["error"]) from None
        except Exception:
            with suppress(Exception):
                await self.disconnect(sid)
            # No cookie, query, UUID, or dependency exception is returned or logged.
            raise socketio.exceptions.ConnectionRefusedError(
                AppError("SERVICE_UNAVAILABLE").envelope()["error"]
            ) from None

    async def disconnect(self, sid: str, reason: str = "") -> None:
        context = await self.runtime.redis.hgetall(socket_key(sid))
        if not context:
            return
        user_id = UUID(context[b"userId"].decode())
        room_id = int(context[b"roomId"])
        participant_id = int(context[b"participantId"])
        async with self.runtime.sessions.begin() as session:
            await session.get(User, user_id, with_for_update=True)
            await session.get(Room, room_id, with_for_update=True)
            current = await self.runtime.redis.eval(
                RELEASE_SOCKET, 2, user_socket_key(str(user_id)), socket_key(sid), sid
            )
            if current:
                await session.execute(
                    update(Participant)
                    .where(Participant.id == participant_id)
                    .values(
                        connection_status=ConnectionStatus.DISCONNECTED,
                        disconnected_at=clock.now_utc(),
                    )
                )
                await self.runtime.redis.hdel(room_key(room_id, "presence"), str(participant_id))
        if current:
            # D-6: sixty seconds without a socket and the slot goes back to the room.
            await self.runtime.rounds.hold_slot(participant_id)
            await self.refresh_lobby(room_id)

    async def ping(self, sid: str, data: Any = None) -> dict[str, Any]:
        try:
            await enforce_limit(self.runtime.redis, "socket", sid, capacity=30, window_sec=10)
            context = await self.runtime.redis.hgetall(socket_key(sid))
            if not context:
                raise AppError("NOT_A_PARTICIPANT")
            refreshed = await self.runtime.redis.eval(
                REFRESH_SOCKET,
                3,
                user_socket_key(context[b"userId"].decode()),
                socket_key(sid),
                room_key(int(context[b"roomId"]), "presence"),
                sid,
                context[b"participantId"].decode(),
                str(SOCKET_TTL_SEC),
                str(clock.now_ms()),
                str(PRESENCE_TTL_SEC),
            )
            if not refreshed:
                raise AppError("NOT_A_PARTICIPANT")
            return {"ok": True}
        except AppError as exc:
            return exc.socket_ack()
        except Exception:
            return AppError("SERVICE_UNAVAILABLE").socket_ack()

    async def _context(self, sid: str) -> dict[bytes, bytes]:
        context: dict[bytes, bytes] = await self.runtime.redis.hgetall(socket_key(sid))
        if not context:
            raise AppError("NOT_A_PARTICIPANT")
        return context

    async def react(self, sid: str, data: Any = None) -> dict[str, Any]:
        """§13.2 `reaction:sent`. The validation order is the requirement (RX-05~08·10)."""
        try:
            await enforce_limit(self.runtime.redis, "socket", sid, capacity=30, window_sec=10)
            context = await self._context(sid)
            actor_id = int(context[b"participantId"])
            payload = data if isinstance(data, dict) else {}
            try:
                submission_id = int(payload["submissionId"])
                reaction_type = ReactionType(payload["type"])
            except (KeyError, TypeError, ValueError):
                raise AppError("INVALID_REQUEST") from None
            async with self.runtime.sessions() as session:
                target = await session.get(Submission, submission_id)
                if target is None:
                    raise AppError("NOT_A_VIEWER")
                current = await session.get(Round, target.round_id)
                if current is None:
                    raise AppError("NOT_A_VIEWER")
                round_id, owner_id = current.id, target.participant_id
                closed = current.status in (RoundStatus.CLOSED, RoundStatus.VOIDED)
            if actor_id not in await round_service.viewer_ids(self.runtime.redis, round_id):
                raise AppError("NOT_A_VIEWER")  # RX-07
            if closed:
                raise AppError("REACTION_CLOSED")  # RX-10
            resolved = await self.runtime.redis.hexists(
                round_key(round_id, "scores"), str(owner_id)
            )
            if not resolved:
                raise AppError("NOT_SCORED_YET")  # RX-06, decided means settled, not successful
            if owner_id == actor_id:
                raise AppError("SELF_REACTION")  # RX-05
            await reactions.toggle_reaction(
                self.runtime.redis, round_id, submission_id, actor_id, reaction_type
            )
            likes, questions = await reactions.count_for(
                self.runtime.redis, round_id, submission_id
            )
            # RX-09: the update says how many, never who.
            await self.send_viewers(
                round_id,
                "reaction:updated",
                {
                    "roundId": str(round_id),
                    "submissionId": str(submission_id),
                    "like": likes,
                    "question": questions,
                },
            )
            return {"ok": True}
        except AppError as exc:
            return exc.socket_ack()
        except Exception:
            return AppError("SERVICE_UNAVAILABLE").socket_ack()

    async def skip(self, sid: str, data: Any = None) -> dict[str, Any]:
        """§10.3 RS-15. The host ends the viewing stage alone; everyone else votes."""
        try:
            await enforce_limit(self.runtime.redis, "socket", sid, capacity=30, window_sec=10)
            context = await self._context(sid)
            participant_id = int(context[b"participantId"])
            room_id = int(context[b"roomId"])
            async with self.runtime.sessions() as session:
                room = await session.get(Room, room_id)
                if room is None or room.current_round_id is None:
                    raise AppError("NOT_CURRENT_ROUND")
                current = await session.get(Round, room.current_round_id)
                if current is None or current.status != RoundStatus.FINALIZED:
                    raise AppError("NOT_CURRENT_ROUND")
                me = await session.get(Participant, participant_id)
                if me is None:
                    raise AppError("NOT_A_PARTICIPANT")
                round_id = current.id
                is_host = room.host_user_id == me.user_id
            viewers = await round_service.viewer_ids(self.runtime.redis, round_id)
            if participant_id not in viewers:
                raise AppError("NOT_A_VIEWER")
            await reactions.toggle_skip(self.runtime.redis, round_id, participant_id)
            voters = await reactions.skip_voters(self.runtime.redis, round_id)
            connected = await self.connected_viewers(round_id, viewers)
            await self.send_viewers(
                round_id,
                "round:skipStatus",
                {
                    "roundId": str(round_id),
                    "skipped": len(voters & connected),
                    "total": len(connected),
                },
            )
            # RS-08: a host skip ends viewing at once; otherwise every connected viewer must vote.
            if is_host or (connected and voters >= connected):
                await self.runtime.rounds.advance(round_id)
            return {"ok": True}
        except AppError as exc:
            return exc.socket_ack()
        except Exception:
            return AppError("SERVICE_UNAVAILABLE").socket_ack()

    async def connected_viewers(self, round_id: int, viewers: set[int]) -> set[int]:
        """RS-15 denominator: viewers currently holding a socket, so a full skip stays reachable."""
        if not viewers:
            return set()
        async with self.runtime.sessions() as session:
            members = list(
                await session.scalars(select(Participant).where(Participant.id.in_(viewers)))
            )
        return {member.id for member in members if await self.current_sid(member) is not None}

    async def current_sid(self, participant: Participant) -> str | None:
        value = await self.runtime.redis.get(user_socket_key(str(participant.user_id)))
        if not value:
            return None
        sid = str(value.decode())
        # A UUID may have moved to a different room. Never send the old room to that sid.
        context = await self.runtime.redis.hgetall(socket_key(sid))
        if not context or context.get(b"participantId") != str(participant.id).encode():
            return None
        return sid

    async def refresh_lobby(self, room_id: int) -> None:
        disconnect_sids: list[str] = []
        async with self.runtime.sessions.begin() as session:
            room = await session.get(Room, room_id, with_for_update=True)
            if room is None:
                return
            members = await service.lobby_participants(session, room.id)
            for participant in members:
                sid = await self.current_sid(participant)
                if sid is None:
                    continue
                if room.status == RoomStatus.CLOSED:
                    await self.emitter.personal("room:closed", sid, {"reason": "host_closed"})
                    disconnect_sids.append(sid)
                elif room.status == RoomStatus.WAITING:
                    snapshot = await service.lobby_snapshot(session, room, participant)
                    snapshot.pop("serverTimeMs")
                    await self.emitter.personal("room:joined", sid, snapshot)
        for sid in disconnect_sids:
            await self.server.disconnect(sid)

    async def send_viewers(
        self,
        round_id: int,
        event: ViewerEvent,
        payload: dict[str, Any] | Callable[[Participant], dict[str, Any]],
        only: set[int] | None = None,
    ) -> None:
        """§13.1 blocks non-viewers physically. Room membership cannot follow a sid across
        Pods, so the round's viewer set decides the recipients and each one is addressed
        by its own sid. Per-viewer payloads (media tokens) need this anyway."""
        viewers = await round_service.viewer_ids(self.runtime.redis, round_id)
        if only is not None:
            # A replay still passes the same gate: the target must be a viewer of this round.
            viewers &= only
        if not viewers:
            return
        async with self.runtime.sessions() as session:
            members = list(
                await session.scalars(select(Participant).where(Participant.id.in_(viewers)))
            )
        for member in members:
            sid = await self.current_sid(member)
            if sid is None:
                continue
            body = payload(member) if callable(payload) else payload
            await self.emitter.viewers(event, sid, body)

    async def send_players(self, room_id: int, event: PlayerEvent, payload: dict[str, Any]) -> None:
        await self.emitter.players(event, room_id, payload)

    async def announce_round(self, room_id: int, *, started: bool = False) -> None:
        """`game:started` reaches the whole room; `round:revealed` is personal (§13.2)."""
        async with self.runtime.sessions.begin() as session:
            room = await session.get(Room, room_id, with_for_update=True)
            if room is None or room.status != RoomStatus.PLAYING or room.current_round_id is None:
                return
            current = await session.get(Round, room.current_round_id)
            if current is None:
                return
            members = await game.active_participants(session, room.id)
            if started:
                await self.emitter.room("game:started", room_id, game.started_view(room, members))
            for participant in members:
                sid = await self.current_sid(participant)
                if sid is None:
                    # A disconnected player restores the round through §14, not a replayed event.
                    continue
                await self.emitter.personal(
                    "round:revealed",
                    sid,
                    game.revealed_view(
                        room, current, participant, len(members), self.runtime.settings
                    ),
                )

    async def shutdown(self) -> None:
        await self.server.shutdown()
        # python-socketio's shutdown only stops Engine.IO, not the Redis listener.
        task = getattr(self.manager, "thread", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await cast(Awaitable[None], task)
        if self.manager.pubsub is not None:
            await self.manager.pubsub.aclose()
        if self.manager.redis is not None:
            await self.manager.redis.aclose()
