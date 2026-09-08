from typing import Any, Literal

import socketio

PersonalEvent = Literal[
    "room:joined",
    "room:closed",
    "session:superseded",
    "round:revealed",
    "round:missed",
    "round:missedUpdate",
]
RoomEvent = Literal["game:started", "round:voided", "game:finished"]
ViewerEvent = Literal[
    "round:finalized",
    "round:closed",
    "submission:scored",
    "reaction:updated",
    "round:skipStatus",
]
PlayerEvent = Literal["submission:status"]
PERSONAL_EVENTS = frozenset(
    (
        "room:joined",
        "room:closed",
        "session:superseded",
        "round:revealed",
        "round:missed",
        "round:missedUpdate",
    )
)
ROOM_EVENTS = frozenset(("game:started", "round:voided", "game:finished"))
VIEWER_EVENTS = frozenset(
    (
        "round:finalized",
        "round:closed",
        "submission:scored",
        "reaction:updated",
        "round:skipStatus",
    )
)
PLAYER_EVENTS = frozenset(("submission:status",))


class Emitter:
    """One place decides which channel an event may use. Result events stay in `viewers`."""

    def __init__(self, server: socketio.AsyncServer) -> None:
        self.server = server

    async def personal(self, event: PersonalEvent, sid: str, payload: dict[str, Any]) -> None:
        if event not in PERSONAL_EVENTS:
            raise ValueError("Unsupported personal event")
        await self.server.emit(event, payload, to=sid)

    async def room(self, event: RoomEvent, room_id: int, payload: dict[str, Any]) -> None:
        """`r:{roomId}` reaches every participant, including those owed no round results."""
        if event not in ROOM_EVENTS:
            raise ValueError("Unsupported room event")
        await self.server.emit(event, payload, room=f"r:{room_id}")

    async def viewers(self, event: ViewerEvent, sid: str, payload: dict[str, Any]) -> None:
        """RS-12: the caller resolved this sid from the round's viewer set, never from the room."""
        if event not in VIEWER_EVENTS:
            raise ValueError("Unsupported viewer event")
        await self.server.emit(event, payload, to=sid)

    async def players(self, event: PlayerEvent, room_id: int, payload: dict[str, Any]) -> None:
        """`r:{roomId}:players` carries submission counts only, never a score (RS-09)."""
        if event not in PLAYER_EVENTS:
            raise ValueError("Unsupported player event")
        await self.server.emit(event, payload, room=f"r:{room_id}:players")
