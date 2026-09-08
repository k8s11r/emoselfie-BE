from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorDefinition:
    status: int
    message: str


ERRORS = {
    "ROOM_NOT_FOUND": ErrorDefinition(404, "방을 찾을 수 없어요"),
    "ROOM_CLOSED": ErrorDefinition(410, "종료된 방이에요"),
    "ROOM_FINISHED": ErrorDefinition(410, "이미 끝난 게임이에요"),
    "ROOM_FULL": ErrorDefinition(409, "방이 가득 찼어요"),
    "NOT_HOST": ErrorDefinition(403, "방장만 할 수 있어요"),
    "NOT_A_PARTICIPANT": ErrorDefinition(403, "먼저 입장해 주세요"),
    "NOT_ENOUGH_PLAYERS": ErrorDefinition(409, "2명 이상이어야 시작할 수 있어요"),
    "GAME_ALREADY_STARTED": ErrorDefinition(409, "게임이 이미 시작됐어요"),
    "INVALID_SETTINGS_COMBINATION": ErrorDefinition(
        409, "이 감정 세트로는 그 라운드 수를 고를 수 없어요"
    ),
    "INVALID_NICKNAME": ErrorDefinition(422, "닉네임은 2~10자로 입력해 주세요"),
    "DEADLINE_PASSED": ErrorDefinition(410, "제출 시간이 끝났어요"),
    "ALREADY_SUBMITTED": ErrorDefinition(409, "이미 제출했어요"),
    "NOT_CURRENT_ROUND": ErrorDefinition(409, "지난 라운드예요"),
    "INVALID_CAPTURE_TOKEN": ErrorDefinition(403, "다시 촬영해 주세요"),
    "PAYLOAD_TOO_LARGE": ErrorDefinition(413, "사진이 너무 커요"),
    "UNSUPPORTED_MEDIA": ErrorDefinition(415, "사진을 읽을 수 없어요"),
    "MEDIA_EXPIRED": ErrorDefinition(410, "사진이 만료됐어요"),
    "NOT_A_VIEWER": ErrorDefinition(403, "이번 라운드 결과는 볼 수 없어요"),
    "NOT_SCORED_YET": ErrorDefinition(409, "아직 채점 중이에요"),
    "SELF_REACTION": ErrorDefinition(403, "내 사진에는 누를 수 없어요"),
    "REACTION_CLOSED": ErrorDefinition(409, "리액션이 마감됐어요"),
    "RATE_LIMITED": ErrorDefinition(429, "잠시 후 다시 시도해 주세요"),
    # Framework failures use the same envelope; additions to §15 are tracked in contracts.md.
    "INVALID_REQUEST": ErrorDefinition(422, "요청 내용을 확인해 주세요"),
    "NOT_FOUND": ErrorDefinition(404, "요청한 경로를 찾을 수 없어요"),
    "METHOD_NOT_ALLOWED": ErrorDefinition(405, "지원하지 않는 요청 방식이에요"),
    "HTTP_ERROR": ErrorDefinition(400, "요청을 처리할 수 없어요"),
    "INTERNAL_ERROR": ErrorDefinition(500, "일시적인 오류가 발생했어요"),
    "FORBIDDEN_ORIGIN": ErrorDefinition(403, "같은 사이트에서 다시 시도해 주세요"),
    "SERVICE_UNAVAILABLE": ErrorDefinition(503, "아직 요청을 처리할 준비가 되지 않았어요"),
}


class AppError(Exception):
    def __init__(
        self, code: str, *, detail: dict[str, Any] | None = None, retry_after: int | None = None
    ) -> None:
        definition = ERRORS[code]
        self.code = code
        self.status = definition.status
        self.message = definition.message
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(code)

    def envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}

    def socket_ack(self) -> dict[str, Any]:
        return {"ok": False, **self.envelope()}
