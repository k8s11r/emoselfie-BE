from typing import Any, Literal

import socketio

LobbyEvent = Literal["room:joined", "room:closed", "session:superseded"]


class Emitter:
    """Only lobby events are implemented. Result events have no broadcast path."""

    def __init__(self, server: socketio.AsyncServer) -> None:
        self.server = server

    async def personal(self, event: LobbyEvent, sid: str, payload: dict[str, Any]) -> None:
        if event not in {"room:joined", "room:closed", "session:superseded"}:
            raise ValueError("Unsupported lobby event")
        await self.server.emit(event, payload, to=sid)
