# 구현 계약과 미확정 항목

2026-09-08, M0 기반 및 세션·방·실시간 대기실 구현. 제품·FE·모델 담당의 합의 기록을 대신하지 않는다.
BE-DEC-01~12의 협의 역할·결정 시점은 [TODO](../TODO.md)와 [PLAN §3](../PLAN.md)을 따른다.
개인 담당자와 확정일은 미배정이다. 외부 담당자에게 메시지를 보내거나 배포하지 않았다.

## 이번 구현의 범위

- §4의 7개 테이블을 초기 migration `0001`로 구현했다. SQLAlchemy 모델과 migration은 독립적이므로 향후 모델 변경이 과거 migration을 바꾸지 않는다.
- `rooms(id, current_round_id)` → `rounds(room_id, id)` 복합 FK를 추가했다. 같은 방의 라운드만 지정할 수 있다. 트랜잭션 종료 시 검증하므로 방 삭제와 자식 CASCADE가 함께 완료된다. 현재 라운드만 삭제하려면 참조를 먼저 해제해야 한다.
- MVP의 `full` 감정 세트, 색상 0~11, 음수 포인트 금지, 라운드 인덱스 1~7 등 이미 정해진 값에 CHECK를 추가했다. 닉네임 문자 정책이나 종료 후 정리 기준은 확정하지 않았다.
- Submission은 명세대로 `GENERATED ALWAYS AS IDENTITY`를 사용한다. 확정 전 외부 ID 할당 및 저장 시점은 G-08에서 결정해야 한다. 지금은 제출 API가 없다.
- 리액션 주체·대상·라운드의 일치, 자기 사진 금지, 참여 권한은 향후 도메인에서 검증해야 한다. 현재 FK만으로 전체 접근제어를 보장하지 않는다.
- `session_factory` 사용자는 `async with sessions.begin()`으로 커밋 경계를 소유한다. 저장소 내부 임의 커밋, Redis/DB를 함께 원자적이라고 가정하는 코드는 없다.
- 로컬 Compose는 조율/이미지 Redis를 별도 프로세스로 실행한다. 이미지 Redis는 RDB/AOF와 볼륨을 사용하지 않는다. 이는 G-10의 개발 환경 구현이며 Infra 운영 계약의 확정은 아니다.
- RedisLease는 `SET NX PX`와 소유 토큰 비교 후 삭제를 제공한다. 임대 만료 뒤에도 실행 중인 작업을 중단하거나 효과를 한 번만 보장하지는 않는다. 상태 CAS·잡 lease 재시도·이벤트 복구는 G-09/BE-034·041에서 구현한다.
- HTTP 프레임워크 오류용 `INVALID_REQUEST`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `HTTP_ERROR`, `INTERNAL_ERROR`를 추가했다. 입력값과 예외 내용을 응답에 반사하지 않는다. §15가 문구를 생략한 `MEDIA_EXPIRED`에는 “사진이 만료됐어요”를 사용한다. 소켓 전용 코드는 향후 HTTP에서도 같은 오류 타입을 사용할 수 있도록 내부 기본 상태를 지정했지만 공개 API를 추가하지 않았다.
- 성공 Socket ack는 `{ok: true}`, 실패는 `{ok: false, error: {code, message, detail}}`다. `detail` 미확정은 null을 유지한다. screen DTO와 명령 중복 처리는 G-02~07 합의 전이며 샘플을 최종 계약으로 만들지 않았다.
- 실제 엔진은 모델 파일이 없거나 로더가 미구현이면 startup에 실패한다. fake는 development/test에서 명시적으로 선택해야 한다. fake readiness의 성공은 실제 모델 검증을 의미하지 않는다.

## 세션·대기실의 현재 구현 선택

G-05·06을 외부 합의 완료로 처리하지 않았다. 진행 요청에 따라 구현 가능한 대기실 경로를 연결했으며 다음 선택은 변경 가능한 BE 구현 기준이다.

- 닉네임은 앞뒤 공백 제거 → NFC 정규화 → Unicode 코드포인트 2~10자, 문자·숫자·내부 ASCII 공백 허용으로 검증한다. 제어문자·제로폭 문자·이모지·기호는 거부한다. FE의 JS 문자열 길이 규칙과 최종 정합성은 G-05에서 맞춘다.
- 입장 전 조회는 §8.2 및 현재 FE `roomPreviewSchema`의 `exists/status/isFull/isHost/settings`를 따른다. 참여자·닉네임·점수는 포함하지 않는다. §6.4의 3필드 제한과의 충돌은 아직 남아 있다. finished는 §15에 따라 `ROOM_FINISHED`, closed는 `ROOM_CLOSED`로 구별한다.
- 반복 입장은 현재 레코드를 그대로 반환한다. left 복귀는 정원부터 다시 검사하고 포인트를 보존한다. waiting에서는 active, playing에서는 waiting_next_game으로 복귀시켜 진행 중 라운드 결과 권한을 획득하지 못하게 한다. G-06의 최종 재입장 정책과는 별도다.
- 색상은 현재 정원을 차지하는 참여자만 기준으로 사용 여부를 계산한다. 복귀자는 원래 색상이 비어 있으면 보존하고, 점유됐으면 빈 색을 재배정한다. left의 과거 점수와 신원을 새 참여자에게 재사용하지 않는다.
- HTTP 입장만으로 연결됐다고 표시하지 않는다. 소켓 연결이 성립하면 connected, 끊기면 disconnected로 표시한다. 60초 유예 후 left와 임시 방장·방 만료 잡은 아직 없다.
- Socket.IO는 현재 waiting 방의 참여자만 허용한다. Redis 원자적 명령으로 UUID의 sid와 문맥을 바꾸고 이전 소켓을 종료한다. 늦은 disconnect는 현재 sid가 같은 경우에만 연결 표시를 내린다.
- 참여자 추가·설정 변경은 현재 FE가 처리하는 `room:joined` 전체 snapshot으로 개인별 갱신한다. 신규 참여자 추가 이벤트의 미확정 B-10을 임의 이름으로 확정하지 않는다. 연결 시 구독 후 다시 DB snapshot을 읽어 HTTP 조회 후 연결 사이의 대기실 변경을 복원한다.
- 대기실 snapshot 조회·갱신은 room 행 잠금으로 직렬화하고 각 수신자의 현재 Redis 문맥을 확인한다. 게임 이벤트 revision/replay·커밋 후 emit 유실 재전달은 아직 구현하지 않았다.
- `GET /state`의 waiting만 제공한다. playing 상태는 결과·참여자 정보를 내려주지 않고 `503 SERVICE_UNAVAILABLE`을 반환한다. 게임 중 FE 복원 완료가 아니다.
- 방 닫기는 waiting에서만 허용한다. 닫은 방의 재호출은 성공하며 소유 슬롯을 반환한다. 게임 도중 중단·결과 보존·잡/이미지 정리는 BE-026에서 이어서 구현한다.
- 방 생성 응답은 기존 playing 방도 그대로 반환한다. 현재 FE 생성 parser가 waiting만 허용하는 차이는 FE 후속 수정이 필요하며 실제 브라우저 연동 완료로 표시하지 않았다.
- `FORBIDDEN_ORIGIN`(403)과 `SERVICE_UNAVAILABLE`(503)를 공통 오류로 추가했다. API 변경 요청의 Origin이 있으면 같은 오리진인지 확인한다. 쿠키 이전 키는 30일 교체 창 종료 시 환경변수에서 제거해야 한다.

## 남은 결정 상태

| 게이트 | 상태 | 다음에 필요한 산출물 |
|---|---|---|
| G-01 | 대기 | 얼굴 모델 아키텍처·weight·라벨 순서·전처리·검증 fixture |
| G-02·03 | 대기 | 업로드 예약/실패 복구·중복 응답·토큰 소비 계약 |
| G-04~07 | 대기 | 화면 DTO·구독 복원·입장 정책·스킵 경계 공동 fixture |
| G-08 | 기반 스키마 작성, 결정 대기 | ID 할당과 finalized/closed 저장 경계 |
| G-09 | 소유 토큰 잠금 구현, 설계 대기 | 재실행·DB CAS·잡/이벤트 유실 복구 |
| G-10 | 로컬 Redis 격리 검증, 운영 합의 대기 | 미디어 서명 만료·프록시 buffering·persistence 검증 |
| G-11 | 대기 | failed 평균·반올림·완전 동점 채점 fixture |
| G-12 | 대기 | 활동 트리거·종료 기준 시각·보존/삭제 경계 |

## Redis 키와 수명

`app/core/redis.py`가 명세 §5의 키와 TTL 상수를 제공한다. 소켓 문맥·presence·레이트 리밋은 Lua로 값과 TTL을 함께 저장한다. 진행 중 라운드의 hash/set 저장과 TTL 설정은 향후 도메인 명령에서 구현해야 한다.
촬영 토큰 TTL은 `deadlineAtMs + 60000` 절대 만료로 적용할 예정이며 아직 소비 명령은 없다.
`room:{roomId}:tempHost` 키를 추가로 예약했지만 복구·TTL 정책은 G-09에서 결정한다.
`sched:timers`는 만료하지 않는다. 이미지 키 삭제와 개인 URL 무효화는 BE-017·046의 후속 작업이다.

## 구현 참고

- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/): 시작·종료 자원 수명 관리.
- [SQLAlchemy constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html): 순환 FK의 별도 생성·삭제.
