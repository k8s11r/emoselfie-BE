# emoselfie Backend — 개발 명세

**버전** v1.0
**작성일** 2026-09-04
**대상 레포** `k8s11r/emoselfie-BE`
**기준 문서** `emoselfie-DOCS/requirements.md` (PRD **v1.1**) · `DECISIONS.md` · `development/Backend Development Guidelines.md`
**PoC 원본** `~/project/p1/femo.py` · `FEMO_WEB.md` — 모델 라벨·전처리의 정본

---

## 0. 이 문서의 위치

이 문서는 PRD를 대체하지 않는다. PRD가 **무엇을 만들지**를 정한다면, 이 문서는 **Backend가 그것을 어떻게 만드는지**를 정한다.

세 문서가 충돌할 경우 다음 순서로 따른다.

```
1. requirements.md (PRD v1.1, 승인본)
2. DECISIONS.md    (C-1 ~ C-12)
3. FEMO_WEB.md / femo.py   ← 모델 계약에 한해 정본
4. Backend Development Guidelines
```

가이드라인은 PRD v0.6~v0.9 시점에 작성되어 v1.0에서 폐기된 요구사항을 일부 담고 있다. 해당 항목은 [11. 가이드라인 반영 차이](#11-가이드라인-반영-차이)에 정리했고, 이 스펙은 모두 **PRD v1.1을 채택**했다.

문서 안의 `[가정]` 표시는 원본 문서에 근거가 없어 이 스펙이 정한 값이다. 이견이 있으면 이 항목부터 고친다.

---

## 1. 목표 (Objective)

Backend는 **게임 상태의 유일한 진실 공급원(Single Source of Truth)** 이다.

12명이 서로 다른 기기·네트워크에서 같은 게임을 보고 있고, 그중 누구도 자기 점수·순위·제출 성공 여부를 스스로 결정할 수 없다. 그 판단을 전부 서버가 한다.

Backend가 소유하는 것:

| 영역 | 근거 |
|---|---|
| 사용자 식별 (UUID) | ID-01~09 |
| 방 상태 · 방장 권한 · 소유 슬롯 | RO-01~17 |
| 참여자 상태 · 연결 상태 | RO-07, PM-11 |
| 게임 · 라운드 · **라운드 마감 시각** | RD-01~10 |
| 제출 인정 여부 (**서버 수신 시각 기준**) | CP-08~10 |
| 감정 판별 · 점수 · 순위 · 포인트 | SC-01~09 |
| 리액션 집계 · 스킵 집계 | RX-01~11, RS-15 |
| **누구에게 어떤 이벤트를 보낼지** | RS-11~14 |

성공 기준 3가지:

1. Client가 어떤 값을 보내도 게임 결과를 조작할 수 없다.
2. 미제출자는 서버가 데이터를 보내지 않아서 결과를 못 본다. 화면에서 가려서가 아니다.
3. Pod가 여러 개로 늘어나도, Pod가 재시작해도 게임이 이어진다.

---

## 2. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| Language | Python 3.11 | |
| API | FastAPI | |
| ASGI | Uvicorn | Pod당 **1 process** (§22) |
| Realtime | python-socketio | Redis manager로 멀티 Pod |
| Validation | Pydantic v2 | 모든 요청·이벤트 페이로드 |
| DB | PostgreSQL | 복원 · 무결성 |
| ORM | SQLAlchemy 2.x + asyncpg | async only |
| Migration | Alembic | |
| Cache/Coord | Redis | 실시간 coordination |
| AI | PyTorch, torchvision, MediaPipe Tasks, Pillow, NumPy | |
| Deploy | Docker, Kubernetes | 매니페스트는 `emoselfie-INFRA` |
| 패키지 관리 | `uv` | **[가정]** — 원본 문서 미지정 |
| Test | pytest, pytest-asyncio, httpx, testcontainers | **[가정]** |
| Lint/Type | ruff, mypy (strict) | **[가정]** |

---

## 3. 모듈 맵

7개 모듈. 각 모듈은 독립적으로 테스트 가능하다.

| ID | 모듈 | 책임 | 상세 |
|---|---|---|---|
| **BE-1** | `identity` | UUID 발급·쿠키·세션 복원 | [spec/BE-1-identity.md](spec/BE-1-identity.md) |
| **BE-2** | `room` | 방·slug·1인1방·방장·참여자 | [spec/BE-2-room.md](spec/BE-2-room.md) |
| **BE-3** | `game-loop` | 게임 시작·라운드·감정 추첨·마감 시각 | [spec/BE-3-game-loop.md](spec/BE-3-game-loop.md) |
| **BE-4** | `inference` | 얼굴 검출·전처리·감정 분류 | [spec/BE-4-inference.md](spec/BE-4-inference.md) |
| **BE-5** | `submission` | 업로드·마감 판정·채점·순위 | [spec/BE-5-submission.md](spec/BE-5-submission.md) |
| **BE-6** | `result-realtime` | 개별 발행·차단·리액션·스킵·최종 결과 | [spec/BE-6-result-realtime.md](spec/BE-6-result-realtime.md) |
| **BE-7** | `ops` | health·Redis·멀티 Pod·레이트 리밋 | [spec/BE-7-ops.md](spec/BE-7-ops.md) |

### 의존 방향

```
        BE-7 ops  ──────────────── (횡단: 모든 모듈이 사용)
             │
   BE-1 identity
        │
        ▼
   BE-2 room
        │
        ▼
   BE-3 game-loop        BE-4 inference   ← BE-1~3과 무관. 병렬 개발 가능
        │                      │
        └──────┬───────────────┘
               ▼
         BE-5 submission
               │
               ▼
        BE-6 result-realtime
```

`BE-4 inference`는 게임 도메인을 전혀 모른다. 입력은 `bytes`, 출력은 `EmotionResult`뿐이다 (가이드 §5). 이 경계 덕분에 PRD의 M1 마일스톤(단일 사용자 프로토타입)을 다른 모듈 없이 먼저 끝낼 수 있다.

### 빌드 순서

| 순서 | 모듈 | PRD 마일스톤 | 완료 판정 |
|---|---|---|---|
| 1 | BE-4 | M1 | JPEG 1장 → 7개 확률 + 얼굴 검출 여부 |
| 2 | BE-1 | M2 | 쿠키 재접속 시 같은 User로 복원 |
| 3 | BE-2 | M2 | 2명이 한 방 대기실에 모임 |
| 4 | BE-3 | M2 | 서버 마감 시각으로 라운드가 흐름 |
| 5 | BE-5 | M2 | 업로드 → 점수 → 순위 |
| 6 | BE-6 | M3 | 실시간 재정렬·리액션·스킵·미제출 차단 |
| 7 | BE-7 | M3/M5 | 2 Pod에서 동일 동작 |

BE-7의 Redis·health는 BE-1 착수 전에 최소 골격만 먼저 세운다. 나중에 넣으면 멀티 Pod 대응이 전면 재작업이 된다.

---

## 4. 프로젝트 구조

```text
emoselfie-BE/
├── SPEC.md
├── spec/                       # 모듈별 상세 명세
├── pyproject.toml
├── alembic.ini
├── Dockerfile
├── migrations/
├── app/
│   ├── main.py                 # FastAPI + Socket.IO ASGI 조립
│   ├── api/                    # HTTP 라우터 (얇게 유지)
│   │   ├── deps.py             # 인증·참여자 조회 의존성
│   │   ├── session.py
│   │   ├── rooms.py
│   │   ├── submissions.py
│   │   └── health.py
│   ├── realtime/
│   │   ├── server.py           # socketio.AsyncServer + Redis manager
│   │   ├── auth.py             # connect 시 UUID → Participant 해석
│   │   ├── emit.py             # 수신자 필터링 (RS-12의 핵심)
│   │   └── handlers/
│   │       ├── room.py
│   │       ├── round.py
│   │       ├── reaction.py
│   │       └── skip.py
│   ├── domain/                 # 순수 규칙. I/O 없음
│   │   ├── user/
│   │   ├── room/
│   │   ├── participant/
│   │   ├── game/
│   │   ├── round/
│   │   ├── submission/
│   │   ├── reaction/
│   │   └── scoring/            # SC-01~09
│   ├── inference/              # 게임 도메인을 모른다
│   │   ├── protocol.py         # EmotionClassifier Protocol
│   │   ├── classifier.py       # ResNet50 구현체
│   │   ├── face.py             # MediaPipe FaceDetector
│   │   ├── preprocess.py
│   │   └── loader.py           # startup 로딩 + warmup
│   ├── db/
│   │   ├── base.py
│   │   ├── models.py
│   │   └── session.py
│   └── core/
│       ├── config.py           # Pydantic Settings
│       ├── redis.py
│       ├── clock.py            # 서버 시각 단일 진입점
│       ├── ratelimit.py
│       └── errors.py
└── tests/
    ├── unit/                   # domain/ · scoring · inference 전처리
    ├── integration/            # API + DB + Redis (testcontainers)
    └── realtime/               # Socket.IO 이벤트 수신자 검증
```

**레이어 규칙**

- `api/`와 `realtime/handlers/`는 얇다. 검증 → 도메인 호출 → 응답. 게임 규칙을 여기에 두지 않는다.
- `domain/`은 DB·Redis·Socket을 import 하지 않는다. 순수 함수와 데이터클래스로 규칙을 표현한다.
- `inference/`는 `domain/`을 import 하지 않는다. 역방향도 마찬가지다. 게임은 `EmotionClassifier` Protocol만 안다.
- `db/models.py`를 API 응답으로 직접 내보내지 않는다. Pydantic 스키마를 거친다.

---

## 5. 커맨드

```bash
uv sync                                    # 의존성 설치
uv run alembic upgrade head                # 마이그레이션
uv run uvicorn app.main:app --reload       # 개발 서버
uv run pytest                              # 전체 테스트
uv run pytest tests/unit -q                # 도메인 규칙만 (빠름)
uv run pytest -m "not slow"                # 모델 로딩 제외
uv run ruff check . && uv run ruff format --check .
uv run mypy app
docker build -t emoselfie-be .
```

`Makefile` 또는 `justfile`로 위 커맨드를 감싼다. **[가정]**

로컬 개발에는 PostgreSQL·Redis용 `docker-compose.yml`을 둔다. 모델 파일은 `./models/`를 볼륨 마운트해 K8s의 `/models`와 같은 구조로 맞춘다.

---

## 6. 코드 스타일

- **async 일관성** — DB·Redis·HTTP 전부 async. 동기 드라이버를 섞지 않는다. PyTorch/MediaPipe 같은 블로킹 연산만 executor로 내보낸다.
- **타입** — `mypy --strict`. `Any`를 쓰면 이유를 주석으로 남긴다.
- **시각** — 항상 timezone-aware UTC. `datetime.now()` 금지, `app/core/clock.py`의 `now()`만 쓴다. 테스트에서 시간을 고정해야 하기 때문이다.
- **에러** — 도메인 예외를 정의하고 `api/`에서 HTTP 상태로 변환한다. 도메인이 `HTTPException`을 raise 하지 않는다.
- **로깅** — 구조화 로그(JSON). 이미지·base64·crop·식별 가능한 파생물은 **어떤 레벨에서도 남기지 않는다** (PV-05, 가이드 §9).
- **요구사항 링크** — 규칙을 구현한 함수 docstring 첫 줄에 요구사항 ID를 적는다.
  ```python
  def resolve_rank_points(rank: int) -> int:
      """SC-02: 1위 100 / 2위 70 / 3위 50 / 그 외 30."""
  ```
- **매직 넘버 금지** — `20`, `12`, `720`, `5.0` 같은 값은 `core/config.py` 또는 도메인 상수로. 근거 ID를 주석에 단다.
- **네이밍** — 도메인 용어는 PRD 14장 용어표를 따른다. `Like`가 아니라 `Reaction`, `점수(score)`와 `포인트(points)`를 섞지 않는다.

---

## 7. 테스트 전략

### 원칙

> 게임 규칙은 UI 테스트가 아니라 Backend unit/integration test에서 검증한다. (가이드 §27)

| 계층 | 대상 | 도구 | 실제 I/O |
|---|---|---|---|
| unit | 채점·순위·동점·감상시간 계산·감정 추첨·전처리 | pytest | 없음 |
| integration | API + DB + Redis. 마감 판정, 1인1방, 재접속 복원 | pytest + testcontainers | 실제 PG/Redis |
| realtime | **누가 어떤 이벤트를 받는가** | socketio async client | 실제 소켓 |
| inference | 고정 이미지 → 기대 라벨, 얼굴 없음, 다중 얼굴 | pytest (`slow` 마크) | 실제 모델 |

### 필수 케이스 (가이드 §27 + v1.0 반영)

```
[식별]      UUID 사용자 복원 · 쿠키 삭제 후 방장 권한 상실(RO-12)
            동일 UUID 다중 탭 → 이전 연결 종료(ID-07)
[방]        1인 1 활성 방 · 기존 방 자동 이동(RO-03)
            게임 종료 시 소유 슬롯 반환 → 즉시 새 방 생성 가능(RO-13/FN-01)
            Host reconnect · Host 60초 이탈 후 임시 위임 · 원 방장 복귀 시 반환
            정원 초과 입장 거부 · 게임 중 입장 시 다음 게임 대기
[라운드]    서버 deadline 생성·전달 · 같은 게임 내 감정 중복 없음(RD-02)
            전원 제출 시 조기 종료(RD-07)
[제출]      마감 1ms 전 upload 인정 · 마감 이후 upload 거부(CP-08~10)
            클라이언트 timestamp 무시 · 중복 제출 거부
            body 전송이 마감을 넘겨도 수신 시작이 마감 전이면 인정
[판별]      Face not detected → NO_FACE 0점(SC-04)
            Multiple faces → 최대 면적 얼굴 선택
            Inference failure / timeout(5초) → 판정 불가, 페널티 없음(SC-05)
[채점]      동점 시 먼저 제출한 쪽 상위(SC-03)
            포인트 = 순위 포인트뿐(SC-06) · 최종 정렬 시점에 확정(SC-09)
[리액션]    사진당 종류별 1회(RX-02) · ♥와 ⁇ 독립(RX-03)
            토글 취소(RX-04) · 자기 사진 금지(RX-05)
            여러 사진에 각각 가능 · 미제출자 행사 불가(RX-07)
            라운드 종료 후 변경 무시(RX-10)
[차단]      미제출자에게 submission:scored / round:finalized /
            reaction:updated / round:skipStatus 미발행(RS-12)
            미제출자가 스냅샷 API를 직접 호출해도 결과 없음
[스킵]      결과를 보는 전원 스킵 시 즉시 다음 라운드(RS-15)
            미제출자는 분모에서 제외 → 진행이 막히지 않음
            토글 취소
[복원]      Reconnect state restore · 라운드 중 새로고침
[멀티팟]    Pod A의 제출이 Pod B의 참여자에게 전달됨
```

### 커버리지

도메인 규칙(`app/domain/`, `app/inference/preprocess.py`)은 **라인 커버리지 90% 이상**. **[가정]** — 나머지 계층은 수치 목표를 두지 않고 위 케이스 목록 충족으로 판정한다.

---

## 8. 경계 (Boundaries)

### 항상 한다

- 모든 게임 판정을 서버에서 한다.
- 제출 마감은 **서버가 요청을 받기 시작한 시각**으로 판정한다 (CP-08).
- 모델은 Pod startup에서 한 번 로드하고 warmup 후 ready를 올린다 (§18, §24).
- 이벤트를 발행하기 전에 **수신자가 그 정보를 받을 권한이 있는지** 판단한다 (§12).
- DB 제약으로 무결성을 보장한다. 애플리케이션 코드만 믿지 않는다 (§16).
- inference 동시 실행을 세마포어로 제한한다 (§25).
- 새 규칙을 구현할 때 요구사항 ID를 연결한다.

### 먼저 물어본다

- 요구사항 ID로 추적되지 않는 기능을 추가할 때.
- 감정 모델·전처리를 바꿀 때 (전처리는 모델 버전의 일부다, §6).
- P1/P2 요구사항을 MVP로 끌어올릴 때.
- 스키마 파괴적 마이그레이션을 넣을 때.
- 새 인프라 컴포넌트(메시지 큐, 오브젝트 스토리지 등)를 도입할 때.

### 절대 하지 않는다

```
❌ Client가 보낸 점수·순위·포인트·방장 여부·제출 시각을 신뢰
❌ Client timestamp로 deadline 판정
❌ 사용자 이미지를 영구 저장 (PostgreSQL BLOB / Object Storage / PV / filesystem)
❌ 로그에 이미지·base64·crop·식별 가능한 파생물 기록
❌ 얼굴 미검출 시 원본 이미지로 감정 판별 (PoC가 그렇게 되어 있다)
❌ 모델 출력을 인덱스로 게임 영역에 전달 — 라벨 키 dict만
❌ 모델을 request마다 로드 / runtime에 모델 다운로드
❌ Room 상태를 특정 Pod의 process memory에만 보관
❌ Socket.IO로 이미지 binary 전송
❌ femo_web.py · Gradio 의존성을 Production에 반입
❌ 미제출자에게 결과를 보낸 뒤 Client에서 가리기
❌ 사진첩·파일 업로드 경로 허용 (CP-03)
❌ 자동 제출 (RD-10)
```

---

## 9. API 계약

FE와 공유하는 표면이다. 변경 시 `emoselfie-FE/SPEC.md`도 함께 고친다.

### HTTP

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| `GET` | `/api/session` | 현재 UUID 세션·저장된 닉네임. 없으면 발급 | ID-01, ID-04 |
| `POST` | `/api/rooms` | 방 생성 `{roundCount, timeLimitSec, emotionSet}` | RO-01, RO-17 |
| `GET` | `/api/rooms/{slug}` | 입장 전 최소 정보(존재·정원·진행 여부). 게임 내용 없음 | RO-15, RO-06 |
| `POST` | `/api/rooms/{slug}/join` | `{nickname}` → 참여자 등록 | RO-05 |
| `GET` | `/api/rooms/{slug}/snapshot` | 재접속 복원용 전체 상태 | ID-05, §15 |
| `PATCH` | `/api/rooms/{slug}/settings` | 라운드 수·제한시간 변경 (방장, `waiting`만) | RO-17, RD-04 |
| `POST` | `/api/rooms/{slug}/start` | 게임 시작 (방장, 2명 이상) | RO-08, PM-10 |
| `POST` | `/api/rooms/{slug}/close` | 방 닫기 (방장) | RO-13 |
| `POST` | `/api/rooms/{slug}/leave` | 퇴장 | RO-16 |
| `POST` | `/api/rooms/{slug}/rounds/{roundId}/submissions` | multipart 이미지 업로드 | CP-03, CP-08 |
| `GET` | `/health/live` `/health/ready` | K8s probe | §24 |

`POST /api/rooms`가 RO-02에 걸리면 에러가 아니다. `200 {redirected: true, slug: "<기존 방>"}`으로 응답하고 FE가 토스트를 띄운다 (RO-03).

### Socket.IO 이벤트

PRD 9장 + C-10 신규 2건. **`like:*` 계열은 존재하지 않는다.**

| 이벤트 | 방향 | 미제출자 발행 |
|---|---|---|
| `room:joined` | S→C | ○ |
| `host:changed` | S→C | ○ |
| `room:closed` | S→C | ○ |
| `participant:updated` | S→C | ○ |
| `participant:removed` | S→C | ○ |
| `permission:changed` | C→S | — |
| `game:started` | S→C | ○ |
| `round:revealed` | S→C | ○ |
| `submission:status` | S→C | ○ (인원 수만) |
| `submission:scored` | S→C | **✕ (RS-12)** |
| `round:finalized` | S→C | **✕** |
| `round:missed` | S→C | 미제출자 **전용** |
| `reaction:sent` | C→S | 거부 (RX-07) |
| `reaction:updated` | S→C | **✕** |
| `round:skip` | C→S | 거부 |
| `round:skipStatus` | S→C | **✕** |
| `round:closed` | S→C | △ 상세 제외 |
| `game:finished` | S→C | ○ (FN-01) |

발행 시 수신자 필터링은 `realtime/emit.py` 한 곳에서만 한다. 핸들러마다 `if` 를 흩뿌리면 RS-12가 언젠가 새어나간다.

---

## 10. 인프라에 요구하는 계약

`emoselfie-INFRA`가 제공해야 하는 것. 매니페스트 자체는 이 스펙의 범위 밖이다.

| 항목 | 요구 |
|---|---|
| 모델 볼륨 | PV를 `/models`에 **read-only** 마운트. 없으면 startup 실패 (§19, §20) |
| 모델 경로 | `/models/emotion/{version}/model.pt`, `/models/face/blaze_face_short_range.tflite` |
| 환경변수 | `EMOTION_MODEL_VERSION`, `EMOTION_MODEL_PATH`, `FACE_MODEL_PATH`, `DATABASE_URL`, `REDIS_URL` |
| Pod 구성 | 1 Pod = 1 Uvicorn process = 1 loaded model (§22) |
| 스케일 | Worker 수가 아니라 replica로 조정 |
| Probe | `/health/live`, `/health/ready`. ready 이전 트래픽 차단 |
| TLS | 전 구간 HTTPS (PM-14) |
| 리소스 | PyTorch 상주 기준으로 memory request/limit 산정 |

Application은 모델을 **다운로드하지 않는다.** 모델 준비는 배포 단계의 책임이다.

---

## 11. 가이드라인 반영 차이

`Backend Development Guidelines.md`는 PRD v1.0 이전에 작성됐다. 아래 항목은 **가이드라인이 아니라 PRD v1.1을 따른다.**

| 가이드라인 | 위치 | PRD v1.1 · 이 스펙 |
|---|---|---|
| 도메인 `Like` | §3, §16 | **`Reaction`** (`type: like\|question`) + **`RoundSkip`** |
| `(roundId, voterParticipantId)` UNIQUE | §16 | `(targetSubmissionId, actorParticipantId, type)` UNIQUE — **라운드당 1표가 아니라 사진당 1회** (RX-02) |
| "Like 1인 1표" 테스트 | §27 | "사진당 종류별 1회 · 여러 사진 가능 · 토글 취소" |
| `like:updated` 미발행 | §13 | `reaction:updated` · `round:skipStatus` 미발행 |
| SSoT 목록의 "좋아요" | §1 | "리액션 집계" — **점수에 반영되지 않음** (RX-01, SC-06) |
| 언급 없음 | — | `round:skip` / `round:skipStatus` 신규 (C-10) |
| 언급 없음 | — | 감상 시간 `min(60, 10 + 인원×2)`초 (RS-08) |
| 언급 없음 | — | 방 slug 최소 12자 URL-safe 난수 (RO-01, C-8) |
| 언급 없음 | — | 제한시간 15/20/30초, 기본 20초 (RD-03/04) |
| 언급 없음 | — | 게임 종료 시 소유 슬롯 자동 반환 (RO-13, FN-01) |
| `femo_web.py` PoC 참조 | §4 | **확보됨** (`~/project/p1/femo.py`, `femo_web.py`, `FEMO_WEB.md`). 라벨·전처리 실측값을 [BE-4](spec/BE-4-inference.md)에 반영 |
| 언급 없음 | — | **모델 출력 라벨 순서가 PRD 5.4 표 순서와 7개 중 5개 불일치.** 이름 기준 매핑 + 기동 시 검증 필수 (PRD v1.1 EM-01) |

---

## 12. 요구사항 추적

| 요구사항 | 모듈 |
|---|---|
| ID-01 ~ ID-09 | BE-1 |
| RO-01 ~ RO-17 | BE-2 |
| PM-07, PM-10, PM-11 | BE-2 |
| RD-01 ~ RD-10 | BE-3 |
| EM-01 ~ EM-04 | BE-3 |
| CP-03, CP-08 ~ CP-10 | BE-5 |
| SC-01 ~ SC-09 | BE-4(판별), BE-5(채점) |
| RS-01 ~ RS-15 | BE-6 |
| RX-01 ~ RX-11 | BE-6 |
| FN-01 ~ FN-03 | BE-6 |
| PV-01 ~ PV-05 | BE-4, BE-5 |
| 7.1 성능 · 7.4 보안 | BE-7 |

FE 담당 요구사항(PM-01~06, CP-01~06, RS-02~03 표현 등)은 `emoselfie-FE/SPEC.md` 참조.

---

## 13. Definition of Done

모듈이 완료로 인정되는 조건. 가이드 §30을 v1.0 기준으로 갱신했다.

- [ ] 구현이 요구사항 ID와 연결되어 있다.
- [ ] Client가 임의 값을 보내도 게임 결과가 바뀌지 않는다 (테스트로 증명).
- [ ] 다수 참가자가 동일한 Room/Round 상태를 받는다.
- [ ] UUID로 현재 상태를 복원할 수 있다.
- [ ] 사용자 사진이 영구 저장되지 않고, 로그에도 남지 않는다.
- [ ] 모델이 startup에서 로드되고, 얼굴 없음과 inference 실패가 구분된다.
- [ ] 미제출자에게 결과 이벤트가 발행되지 않는다 (realtime 테스트로 증명).
- [ ] Pod restart 이후 정상 복구되고, readiness 이전에 트래픽을 받지 않는다.
- [ ] 2 Pod 환경에서 realtime이 동일하게 동작한다.
- [ ] `ruff` · `mypy` · `pytest` 전부 통과.

---

## 14. 최우선 순서

막히면 이 순서로 판단한다.

1. 서버가 게임 상태의 유일한 기준인가
2. 라운드와 제출 마감이 정확한가
3. 재접속 시 사용자와 게임 상태가 복원되는가
4. 실시간 이벤트가 **올바른 사용자에게만** 전달되는가
5. 이미지가 영구 저장되지 않는가
6. inference 결과가 게임 규칙과 정확히 연결되는가
7. inference가 다른 API와 realtime을 막지 않는가
8. Pod가 여러 개로 늘어나도 동일하게 동작하는가
