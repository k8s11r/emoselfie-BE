from typing import Any, Literal

import socketio

PersonalEvent = Literal[
    "room:joined", "room:closed", "session:superseded", "round:revealed", "round:missed"
]
RoomEvent = Literal["game:started", "round:voided", "game:finished"]
PERSONAL_EVENTS = frozenset(
    ("room:joined", "room:closed", "session:superseded", "round:revealed", "round:missed")
)
ROOM_EVENTS = frozenset(("game:started", "round:voided", "game:finished"))


class Emitter:
    """Lobby, personal round and room-wide game events. Result channels are still missing."""

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
