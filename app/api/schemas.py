from typing import Literal

from pydantic import Field

from app.core.schemas import PublicId, WireModel
from app.domain.enums import ConnectionStatus, ParticipantStatus, RoomStatus


class NicknameInput(WireModel):
    nickname: str = Field(max_length=128, strict=True)


class MeResponse(WireModel):
    nickname: str | None
    has_active_room: bool
    active_room_slug: str | None


class SettingsInput(WireModel):
    round_count: Literal[3, 5, 7] = 5
    time_limit_sec: Literal[15, 20, 30] = 20


class SettingsPatch(WireModel):
    round_count: Literal[3, 5, 7] | None = None
    time_limit_sec: Literal[15, 20, 30] | None = None


class RoomSettings(SettingsInput):
    emotion_set: Literal["full"] = "full"


class CreatedRoom(WireModel):
    slug: str
    status: RoomStatus
    is_host: Literal[True] = True
    settings: RoomSettings
    existing: bool


class RoomPreview(WireModel):
    exists: Literal[True] = True
    status: RoomStatus
    is_full: bool
    is_host: bool
    settings: SettingsInput


class ParticipantJoined(WireModel):
    participant_id: PublicId
    nickname: str
    color_tag: int
    status: ParticipantStatus
    is_host: bool
    socket_path: str = "/socket.io"


class ParticipantView(WireModel):
    participant_id: PublicId
    nickname: str
    color_tag: int
    connection_status: ConnectionStatus
    status: ParticipantStatus
    is_host: bool


class RoomView(WireModel):
    slug: str
    status: RoomStatus
    settings: RoomSettings


class MeInRoom(WireModel):
    participant_id: PublicId
    nickname: str
    is_host: bool
    status: ParticipantStatus
    total_points: int


class LobbyState(WireModel):
    room: RoomView
    me: MeInRoom
    participants: list[ParticipantView]
    game: None = None
    server_time_ms: int
