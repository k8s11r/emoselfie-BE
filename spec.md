# 감정 표현 셀카 게임 — Backend 구현 명세

**문서 버전** v1.0
**작성** 2026-09-07
**상태** 구현 착수 가능
**상위 문서** [requirements.md](../emoselfie-DOCS/requirements.md) v1.0 (PRD), [DECISIONS.md](../emoselfie-DOCS/DECISIONS.md), [Backend Development Guidelines.md](../emoselfie-DOCS/development/Backend%20Development%20Guidelines.md)

---

## 0. 이 문서의 범위

PRD는 **무엇을** 만들지, Backend Guidelines는 **어떤 원칙으로** 만들지를 정한다.
이 문서는 그 둘을 받아 **코드로 옮길 수 있는 수준의 계약**을 정의한다.

| 다루는 것 | 다루지 않는 것 |
|---|---|
| 상태 머신, 스키마, API·이벤트 페이로드, 채점 알고리즘, 타이머 규칙, 접근 제어, 에러 코드 | 화면 디자인, FE 컴포넌트 구조, 모델 학습, K8s 매니페스트 실물 |

**우선순위 규칙** — 세 문서가 어긋나면 **PRD v1.0 > DECISIONS.md > Backend Guidelines** 순으로 따른다. Backend Guidelines는 PRD v0.9 시점에 작성되어 일부 항목이 낡았다(§1.3 참조).

모든 요구사항 참조는 PRD의 ID(`RO-02`, `SC-05` 등)를 그대로 쓴다. Definition of Done(가이드 30장)의 "요구사항 ID와 구현이 연결되어 있다"를 만족시키기 위해, 이 문서의 각 절은 자신이 구현하는 ID를 명시한다. 역방향 추적표는 §19에 있다.

---

## 1. 선행 결정 사항

### 1.1 이번에 확정한 결정

PRD·가이드라인만으로는 구현이 갈리는 지점이 있어 8건을 확정했다. 각 항목은 이 문서 본문에서 다시 상세화된다.

| ID | 쟁점 | 결정 | 근거 |
|---|---|---|---|
| **D-1** | 결과 화면의 타인 사진 전달 방식 (PV-02 ↔ 가이드 9장 충돌) | **Redis 임시 캐시 + 서명 URL.** 추론 후 재인코딩한 JPEG를 Redis에 TTL로 보관하고, 열람 권한자에게만 단기 서명 URL로 서빙한다. 라운드 종료 시 즉시 `DEL` | 다인 결과 화면은 서버 경유가 물리적으로 불가피하다. 가이드 9장의 금지 대상은 **영구 저장**(BLOB·Object Storage·PV·영구 filesystem)이며 Redis TTL 캐시는 그 목록에 없다. multi-pod(가이드 23장)에서도 동작하고, RS-12 차단을 서버에서 강제할 수 있다 |
| **D-2** | SC-04·SC-05의 순위 포인트 (PRD 미정의) | **얼굴 미검출 = "그 외" 30점 고정. 엔진 실패 = 해당 라운드 정상 채점자들의 순위 포인트 평균(반올림), 순위 정렬에서는 제외** | SC-04는 0점이지만 제출은 했으므로 미제출(0포인트)과 구분해야 한다. SC-05는 "총점 계산 시 페널티를 주지 않는다"를 문자 그대로 구현한 것이 평균 보정이다 |
| **D-3** | FE/BE 오리진 구조 | **동일 오리진.** Ingress가 `/api/*`, `/socket.io/*`, `/media/*`를 BE로 경로 분기 | ID-02의 `SameSite=Lax`를 수정 없이 만족한다. CORS 불필요, Socket.IO 핸드셰이크에 쿠키가 자동 동봉, iOS Safari의 서드파티 쿠키 차단과 무관 |
| **D-4** | `round:missed`의 `nextRoundAt` (마감 시점에는 미확정) | **2단계 발행.** 마감 즉시 `round:missed`(시각 없음, `phase:"scoring"`) → 최종 정렬 확정 후 `round:missedUpdate`(`nextRoundAt` 포함) | 마감~정렬 사이 최대 5초 이상 미제출자 화면이 비어 UI 원칙 6.5("무한 로딩 금지")를 위반한다 |
| **D-5** | 판별 엔진 전체 장애 시 "라운드 무효 처리"의 정의 | **무효 라운드는 재시도하지 않고 폐기한 뒤 다음 라운드로 진행한다.** 실제 진행 라운드 수가 그만큼 줄어든다. 3회 연속 무효 시 게임 중단 후 현재 순위 공개 | 재실행은 같은 감정을 다시 못 쓰고(RD-02) 대기가 길어진다. 진행을 멈추지 않는 쪽이 완주율(G2)에 유리하다 |
| **D-6** | 정원·인원 부족 판정 기준 | **`Participant.status` 기준.** `connectionStatus`는 표시용이며 판정에 쓰지 않는다. 연결이 끊긴 참여자는 **60초** 유예 후 `status=left`로 내려 정원을 반납하고, 그 시점에 "2명 미만 종료"를 판정한다 | 7.2의 "10초 이내 재접속 복귀"를 지키면서 유령 참여자의 정원 점유도 막는다. 유예 60초는 방장 유예(RO-10)와 같은 값을 쓴다 |
| **D-7** | 닉네임 중복 (PRD 13장 미결 2) | **같은 방 내 중복 허용.** 서버는 유일성을 강제하지 않고 길이(2~10자)·문자 종류만 검증한다. 구분은 `participantId`와 `colorTag`가 담당한다 | 오프라인 모임이라 서로 누가 누군지 안다. 입장 단계의 마찰을 늘리면 G1과 어긋난다 |
| **D-8** | 라운드 진행 중 상태의 저장 위치 | **Redis 우선 쓰기 + 라운드 확정 시 PostgreSQL 반영.** 제출 현황·점수·리액션·스킵은 Redis가 진실이고, `round:finalized` 시점에 `Submission`·`Reaction`·`totalPoints`를 한 트랜잭션으로 커밋한다 | 가이드 17장의 역할 분리를 지키면서 p95 3초를 확보한다. Redis 유실 시 진행 중인 라운드 1개만 잃고 이전 라운드 결과는 DB에 남는다 |

### 1.2 PRD에서 그대로 승계하는 확정 사항

| 항목 | 값 |
|---|---|
| 최소 인원 / 정원 | 2명 / 12명 (RO-06, PM-10) |
| 라운드 수 | 3 / 5 / 7, 기본 5 (RD-01) |
| 제한시간 | 15 / 20 / 30초, 기본 20 (RD-03, RD-04) |
| 감정 세트 | 전체 7종 고정. `easy`(5종)는 P1이므로 MVP 미구현 (EM-01, EM-04) |
| 사진 노출 범위 | 제출자 전원 공개 (RS-02, 6.3에서 사실상 확정) |
| 포인트 배점 | 순위 기반 100 / 70 / 50 / 30 (SC-02). 점수 누적 방식은 베타 A/B 대상 |
| 방장 비밀번호 | MVP 미도입 (PRD 13장 미결 8) |

### 1.3 Backend Guidelines의 낡은 서술 — 이 문서에서 대체

가이드라인은 PRD v0.9 시점에 작성되어 v1.0의 **C-4(좋아요 → 리액션)** 변경이 반영되지 않았다.

| 가이드 위치 | 낡은 서술 | 대체 |
|---|---|---|
| 3장 도메인 목록 | `Like` | `Reaction`, `RoundSkip` |
| 13장 이벤트 | `like:updated` | `reaction:updated` |
| 16장 제약 | `(roundId, voterParticipantId) UNIQUE` — 라운드당 1표 | `(targetSubmissionId, actorParticipantId, type) UNIQUE` — 사진당 종류별 1회 (RX-02·03) |
| 27장 테스트 | "Like 1인 1표", "Self like 금지" | "리액션 사진당 종류별 1회", "복수 사진 가능", "자기 사진 리액션 금지", "토글 취소" |

나머지 가이드라인 조항(서버 권위, deadline 기준, 이미지 비영구 저장, 모델 startup 로딩, 추론 동시성 제한 등)은 전부 유효하며 이 문서가 구체화한다.

---

## 2. 아키텍처

### 2.1 배치

```
                    ┌──────────────────────────┐
   Browser ───TLS──▶│  Ingress (emoselfie.app) │
                    └───┬──────────────────┬───┘
                        │ /                │ /api, /socket.io, /media
                        ▼                  ▼
                  Static (FE)     ┌────────────────┐   ┌────────────────┐
                                  │  BE Pod A      │   │  BE Pod B      │
                                  │  FastAPI       │   │  FastAPI       │
                                  │  Socket.IO     │   │  Socket.IO     │
                                  │  Emotion Model │   │  Emotion Model │
                                  │  FaceDetector  │   │  FaceDetector  │
                                  └───┬────────┬───┘   └───┬────────┬───┘
                                      │        │           │        │
                                      ▼        └─────┬─────┘        ▼
                              ┌────────────┐         ▼      ┌──────────────┐
                              │ PostgreSQL │   ┌──────────┐ │ Model Volume │
                              │ 복원·무결성 │   │  Redis   │ │  (RO mount)  │
                              └────────────┘   │ 실시간   │ └──────────────┘
                                               │ 조율     │
                                               └──────────┘
```

동일 오리진(D-3)이므로 CORS 설정이 없고 Socket.IO 핸드셰이크에 `HttpOnly` 쿠키가 자동 동봉된다.

> **다른 오리진을 택하게 될 경우의 변경점** — 서브도메인 분리라면 쿠키에 `Domain=.emoselfie.app`를 추가하고 CORS `allow_credentials=true` + `allow_origins` 화이트리스트, Socket.IO `cors_allowed_origins`를 설정한다. 완전히 다른 도메인이라면 `SameSite=None; Secure`가 필수이며 PRD ID-02의 문구 수정이 선행되어야 한다.

### 2.2 모듈 구조

가이드 3장의 Modular Monolith를 따른다.

```text
app/
├── main.py                  # ASGI 조립, lifespan(모델 로드 → warmup → ready)
├── core/
│   ├── config.py            # Pydantic Settings (§20 환경변수)
│   ├── security.py          # 쿠키 발급/검증, 서명 토큰(HMAC) 발급/검증
│   ├── clock.py             # 단일 시각 소스. 전 코드가 여기만 사용
│   ├── errors.py            # AppError 계층 → §15 에러 코드
│   └── ratelimit.py         # Redis 기반 토큰 버킷
├── api/
│   ├── deps.py              # current_user / current_participant / require_host
│   ├── session.py           # GET /api/me, PATCH /api/me
│   ├── rooms.py             # 생성·조회·설정·시작·닫기·입장·퇴장
│   ├── submissions.py       # 이미지 업로드
│   ├── media.py             # 서명 URL 이미지 서빙
│   └── health.py            # /health/live, /health/ready
├── realtime/
│   ├── server.py            # python-socketio AsyncServer + Redis manager
│   ├── rooms.py             # Socket.IO room 네이밍·가입·탈퇴 (§13)
│   ├── auth.py              # 핸드셰이크 인증, ID-07 단일 연결 강제
│   ├── emitter.py           # 모든 S→C 발행의 단일 창구 (권한 검사 포함)
│   └── handlers/
│       ├── connection.py    # connect / disconnect / presence
│       ├── reaction.py      # reaction:sent
│       ├── skip.py          # round:skip
│       └── permission.py    # permission:changed
├── domain/
│   ├── user/                # UUID 발급, 닉네임, 만료 정리
│   ├── room/                # 생성·소유 슬롯·방장 위임·소멸
│   ├── participant/         # 입장·퇴장·연결 상태·복원
│   ├── game/                # 게임 상태 머신, 감정 시퀀스
│   ├── round/               # 라운드 수명주기, deadline, 최종 정렬
│   ├── scoring/             # §11 채점 알고리즘 (순수 함수)
│   ├── reaction/
│   └── scheduler/           # §10.4 분산 타이머
├── inference/
│   ├── protocol.py          # EmotionClassifier Protocol, EmotionResult
│   ├── pipeline.py          # decode → detect → crop → preprocess → model
│   ├── loader.py            # startup 로드, warmup
│   └── runner.py            # Semaphore 동시성 제어 + 타임아웃
├── media/
│   └── cache.py             # D-1 Redis 이미지 캐시
└── db/
    ├── models.py            # SQLAlchemy 2.x
    ├── session.py           # asyncpg 엔진
    └── migrations/          # Alembic
```

**의존 방향** — `api`·`realtime` → `domain` → `db`·`media`·`inference`. `domain`은 FastAPI·Socket.IO 타입을 import하지 않는다. `domain/scoring`은 I/O가 없는 순수 함수 모듈로 두어 단위 테스트를 쉽게 한다(가이드 27장).

### 2.3 시각 처리

- 서버 내부의 모든 시각은 **UTC `datetime`**, 저장은 `TIMESTAMPTZ`.
- 클라이언트로 나가는 시각은 전부 **epoch milliseconds (`int`)**. ISO 문자열 파싱 편차로 500ms 동기화 목표(7.2)를 깨뜨리지 않기 위해서다.
- 시각 소스는 `core/clock.py`의 `now_ms()` 하나만 쓴다. 테스트에서 이 함수만 고정하면 전 타이머 로직을 결정론적으로 검증할 수 있다.
- Pod 간 시계 편차는 NTP에 의존한다. 편차가 커도 **deadline은 절대 시각으로 한 번만 계산해 저장**하므로, 어느 Pod가 판정해도 결과가 같다.

---

## 3. 상태 머신

### 3.1 Room

```
                     ┌──────────────────────────────┐
   POST /api/rooms   │                              │
        └──────▶ waiting ──start──▶ playing ──끝──▶ finished ──30분──▶ (삭제)
                     │                  │                ▲
                     │ 방장 닫기        │ 인원 2명 미만   │
                     │ / 30분 무활동    │ / 엔진 3연속 무효
                     ▼                  │                │
                  closed ◀──────────────┘                │
                     │                                   │
                     └──── 30분 후 삭제 ◀─────────────────┘
```

| 상태 | 의미 | 소유 슬롯 | 입장 |
|---|---|---|---|
| `waiting` | 대기실 | 점유 | 가능 (정원 내) |
| `playing` | 게임 진행 중 | 점유 | 가능하나 `waiting_next_game` (RO-09) |
| `finished` | 최종 결과 도달 | **반납** (RO-13, FN-01) | 거부 (`ROOM_FINISHED`) |
| `closed` | 방장이 닫음 / 무활동 만료 | **반납** | 거부 (`ROOM_CLOSED`) |

- `finished`·`closed` 진입 시 `hostUserId`가 소유 슬롯에서 즉시 빠진다(§4.2 부분 유니크 인덱스). 방 레코드는 결과 조회를 위해 남는다.
- `lastActiveAt` 갱신 트리거: 참여자 입·퇴장, 설정 변경, 게임 시작, 제출, 리액션, 스킵, 소켓 connect. 단순 폴링은 갱신하지 않는다.
- RO-14의 30분 만료는 §10.4 스케줄러가 처리한다.

### 3.2 Participant

```
   입장 ──▶ active ──────────────────┐
             │  ▲                    │ 게임 진행 중 입장
             │  │ 다음 게임 시작     ▼
             │  └──────────── waiting_next_game
             │                       │
   퇴장/유예 만료/권한 철회           │ 퇴장
             ▼                       ▼
            left ◀────────────────────
             │
             └── 같은 링크 재입장 ──▶ active (누적 포인트 유지, RO-16 / PM-11)
```

| 필드 | 값 | 용도 |
|---|---|---|
| `status` | `active` \| `waiting_next_game` \| `left` | **정원·인원 판정의 유일한 기준** (D-6) |
| `connectionStatus` | `connected` \| `disconnected` | 대기실 표시용 (RO-07). 판정에 쓰지 않는다 |

- `disconnected` 진입 시 `disconnectedAt`을 기록하고 60초 후 `status=left` 전환 잡을 예약한다. 그 안에 재접속하면 잡을 취소한다(D-6).
- `left`로 내려가도 레코드와 `totalPoints`는 남는다. 같은 UUID로 재입장하면 같은 레코드를 `active`로 되살린다(RO-16, PM-11).
- 정원(RO-06) 계산 = `status IN ('active','waiting_next_game')` 인원 수.

### 3.3 Game / Round

게임은 Room의 `playing` 구간이며 별도 엔티티를 두지 않는다. 라운드가 게임의 진행 단위다.

```
   revealed ──3초──▶ capturing ──마감/전원제출──▶ scoring ──전원 확정──▶ finalized
                                                     │                      │
                                                     │ 제출자 전원 실패      │ 감상 종료
                                                     ▼                      ▼
                                                   voided                 closed
```

| 상태 | 진입 조건 | 나가는 조건 |
|---|---|---|
| `revealed` | 라운드 생성 + `round:revealed` 발행 | 3초 카운트다운 종료 (RD-03) |
| `capturing` | 카운트다운 종료 | `deadlineAt` 도달 **또는** `active` 참여자 전원 제출 (RD-07) |
| `scoring` | 제출 마감 | 모든 제출의 추론이 확정(성공/무검출/실패/타임아웃) |
| `finalized` | 최종 정렬 + 포인트 확정 (SC-09) | 감상 시간 만료 / 방장 스킵 / 열람자 전원 스킵 (RS-08, RS-15) |
| `voided` | `scoring`에서 제출자 전원이 `failed` (D-5) | 즉시 다음 라운드 |
| `closed` | 감상 종료 | 다음 라운드 또는 게임 종료 |

`capturing` 진입은 별도 이벤트를 쓰지 않는다. `round:revealed`가 `countdownEndsAt`과 `deadlineAt`을 모두 담아 클라이언트가 자체 전환한다(RD-08).

---

## 4. PostgreSQL 스키마

가이드 16장("DB Constraint를 적극적으로 사용한다")에 따라, 애플리케이션 코드로만 지킬 수 있는 불변식은 최대한 제약으로 내린다.

### 4.1 ENUM

```sql
CREATE TYPE room_status        AS ENUM ('waiting','playing','finished','closed');
CREATE TYPE participant_status AS ENUM ('active','waiting_next_game','left');
CREATE TYPE connection_status  AS ENUM ('connected','disconnected');
CREATE TYPE round_status       AS ENUM ('revealed','capturing','scoring','finalized','voided','closed');
CREATE TYPE submission_status  AS ENUM ('submitted','no_face','failed','missed');
CREATE TYPE reaction_type      AS ENUM ('like','question');
CREATE TYPE emotion_label      AS ENUM ('happy','sad','angry','surprise','neutral','disgust','fear');
```

`submission_status`는 PRD 8장의 4값을 그대로 쓴다. `submitted`는 "정상 채점됨"을 뜻한다.

### 4.2 테이블

```sql
CREATE TABLE users (
    uuid        UUID PRIMARY KEY,
    nickname    TEXT,                             -- ID-04. 최초에는 NULL
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_last_seen ON users (last_seen_at);   -- ID-09 정리 배치

CREATE TABLE rooms (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invite_slug    TEXT NOT NULL UNIQUE,                     -- RO-01. 12자 URL-safe
    host_user_id   UUID NOT NULL REFERENCES users(uuid),
    status         room_status NOT NULL DEFAULT 'waiting',
    round_count    SMALLINT NOT NULL DEFAULT 5,
    time_limit_sec SMALLINT NOT NULL DEFAULT 20,
    emotion_set    TEXT NOT NULL DEFAULT 'full',             -- 'full' | 'easy'(P1)
    emotion_sequence emotion_label[] NOT NULL DEFAULT '{}',  -- 게임 시작 시 확정
    current_round_id BIGINT,
    consecutive_voided SMALLINT NOT NULL DEFAULT 0,          -- D-5
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_round_count    CHECK (round_count IN (3,5,7)),
    CONSTRAINT ck_time_limit     CHECK (time_limit_sec IN (15,20,30)),
    CONSTRAINT ck_slug_len       CHECK (char_length(invite_slug) >= 12)
);

-- RO-02: 한 사용자가 동시에 소유할 수 있는 활성 방은 1개
CREATE UNIQUE INDEX uq_room_active_owner
    ON rooms (host_user_id)
    WHERE status IN ('waiting','playing');

CREATE INDEX idx_rooms_last_active ON rooms (last_active_at)
    WHERE status IN ('waiting','playing','finished');        -- RO-14 소멸 배치

CREATE TABLE participants (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id           BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id           UUID   NOT NULL REFERENCES users(uuid),
    nickname          TEXT   NOT NULL,
    color_tag         SMALLINT NOT NULL,                     -- 0..11
    status            participant_status NOT NULL DEFAULT 'active',
    connection_status connection_status  NOT NULL DEFAULT 'connected',
    disconnected_at   TIMESTAMPTZ,
    total_points      INTEGER NOT NULL DEFAULT 0,
    best_round_score  NUMERIC(4,1) NOT NULL DEFAULT 0,       -- SC-06 동점 처리
    joined_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_nickname_len CHECK (char_length(nickname) BETWEEN 2 AND 10),
    CONSTRAINT uq_room_user    UNIQUE (room_id, user_id)     -- 같은 방 재입장은 같은 레코드
);
CREATE INDEX idx_participants_room_status ON participants (room_id, status);

CREATE TABLE rounds (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id        BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    index          SMALLINT NOT NULL,                        -- 1-based
    target_emotion emotion_label NOT NULL,
    status         round_status NOT NULL DEFAULT 'revealed',
    revealed_at    TIMESTAMPTZ NOT NULL,
    deadline_at    TIMESTAMPTZ NOT NULL,                     -- RD-08. 절대 시각
    finalized_at   TIMESTAMPTZ,
    viewing_ends_at TIMESTAMPTZ,                             -- RS-08
    closed_at      TIMESTAMPTZ,

    CONSTRAINT uq_round_index UNIQUE (room_id, index),
    CONSTRAINT ck_deadline    CHECK (deadline_at > revealed_at)
);

CREATE TABLE submissions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id      BIGINT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    participant_id BIGINT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    status        submission_status NOT NULL,
    received_at   TIMESTAMPTZ,                               -- CP-08. 서버 수신 시각
    target_score  NUMERIC(4,1),                              -- SC-01. 0.0~100.0
    top_emotions  JSONB,                                     -- RS-10. [{label,score}] 상위 3
    rank          SMALLINT,                                  -- failed/missed는 NULL
    rank_points   SMALLINT NOT NULL DEFAULT 0,
    like_count    SMALLINT NOT NULL DEFAULT 0,
    question_count SMALLINT NOT NULL DEFAULT 0,

    CONSTRAINT uq_round_participant UNIQUE (round_id, participant_id),  -- 중복 제출 금지
    CONSTRAINT ck_score_range CHECK (target_score IS NULL
                                     OR (target_score >= 0 AND target_score <= 100)),
    -- 제출한 것만 수신 시각을 갖는다
    CONSTRAINT ck_received CHECK (
        (status = 'missed'  AND received_at IS NULL)
     OR (status <> 'missed' AND received_at IS NOT NULL))
);

CREATE TABLE reactions (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id              BIGINT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    actor_participant_id  BIGINT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    target_submission_id  BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    type                  reaction_type NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- RX-02·03: 사진당 종류별 1회. 종류가 다르면 같은 사진에 둘 다 가능
    CONSTRAINT uq_reaction UNIQUE (target_submission_id, actor_participant_id, type)
);

CREATE TABLE round_skips (
    round_id       BIGINT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    participant_id BIGINT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (round_id, participant_id)                   -- RS-15
);
```

**RX-05(자기 사진 리액션 금지)는 DB 제약으로 표현할 수 없다.** `reactions.actor_participant_id ≠ submissions.participant_id` 비교가 다른 테이블을 참조하기 때문이다. 애플리케이션 레이어에서 검증하고, 전용 테스트를 둔다(§18).

### 4.3 보존과 삭제

| 데이터 | 삭제 시점 | 구현 |
|---|---|---|
| 이미지 바이너리 | 라운드 종료 즉시 | Redis `DEL` (§12) — DB에 없음 (PV-01) |
| Room·Round·Submission·Reaction·RoundSkip | 방 소멸 30분 후 | `rooms` 삭제 → `ON DELETE CASCADE` |
| Participant | 방과 함께 | 동일 |
| User | 마지막 접속 1년 경과 | 일 1회 배치 (ID-09) |

`users`는 방보다 오래 살아야 하므로 `participants.user_id`에는 `ON DELETE CASCADE`를 걸지 않는다.

---

## 5. Redis 키스페이스

가이드 17장의 역할 분리를 키 이름으로 강제한다. **PostgreSQL에 없으면 복원할 수 없는 데이터는 Redis에만 두지 않는다.**

| 키 | 타입 | TTL | 용도 |
|---|---|---|---|
| `sock:{sid}` | Hash | 2h | `{userId, participantId, roomId}` 소켓 컨텍스트 |
| `user:sock:{userId}` | String | 2h | 현재 유효한 sid **하나**. ID-07의 단일 연결 강제 |
| `room:{roomId}:presence` | Hash | 1h | `participantId → lastPingMs` |
| `round:{roundId}:submitted` | Hash | 30m | `participantId → receivedAtMs`. CP-08 판정 결과 |
| `round:{roundId}:scores` | Hash | 30m | `participantId → {status, targetScore, topEmotions}` |
| `round:{roundId}:rx:{type}` | Hash | 30m | `submissionId → count` |
| `round:{roundId}:rx:actors` | Set | 30m | `"{submissionId}:{actorId}:{type}"` — 토글 판정 |
| `round:{roundId}:skips` | Set | 30m | `participantId`. RS-15 |
| `round:{roundId}:tok:{participantId}` | String | 마감+60s | 촬영 세션 토큰 1회 사용 마킹 (CP-03) |
| `img:{roundId}:{participantId}` | String (bytes) | 라운드 종료 시 DEL / 안전망 180s | D-1 이미지 캐시 |
| `sched:timers` | ZSet | — | 분산 타이머 큐 (§10.4) |
| `sched:lock:{jobId}` | String | 30s | 잡 단일 실행 락 |
| `lock:room:{roomId}` | String | 5s | 방 상태 전이 직렬화 |
| `rl:{scope}:{key}` | String | 규칙별 | 레이트 리밋 (§16) |

- 이미지 키는 **바이너리 전용 Redis DB 인덱스**(또는 별도 인스턴스)에 둔다. 조율용 키와 메모리 압력을 섞지 않기 위해서다.
- `maxmemory-policy`는 조율용 DB에 `noeviction`을 쓴다. 게임 진행 상태가 조용히 증발하면 안 된다. 이미지 DB는 `allkeys-lru`도 허용한다(유실 시 사진만 안 보이고 점수는 무사).
- Socket.IO의 pub/sub은 `AsyncRedisManager`가 자체 채널을 쓴다(가이드 23장).

---

## 6. 인증과 세션

### 6.1 UUID 쿠키 (ID-01·02·03·06)

```
쿠키 이름   es_uid
값          UUID v4 문자열 + "." + HMAC-SHA256(SECRET, uuid)[:16] (base64url)
속성        HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=31536000
```

- 모든 요청과 Socket.IO 핸드셰이크를 지나는 미들웨어가 쿠키를 검사한다. 없거나 서명이 깨졌으면 새 UUID를 발급하고 `Set-Cookie`를 내린다(ID-06: 새 사용자로 취급).
- HMAC 서명은 DB 조회 없이 위조 쿠키를 걸러내기 위한 것이다. **권한 판정에는 쓰지 않는다** — ID-03과 가이드 14장에 따라 방장 여부는 항상 `rooms.host_user_id == current_user.uuid`를 DB에서 조회해 확인한다.
- 클라이언트로 나가는 실시간 이벤트에는 UUID를 절대 싣지 않는다(ID-08). 노출 식별자는 `participantId`뿐이다.
- `SECRET`은 K8s Secret으로 주입한다. 로테이션 시 이전 키로도 검증하는 2키 창을 30일 유지한다.

### 6.2 Socket.IO 핸드셰이크와 단일 연결 (ID-07)

```
connect
  ├─ 핸드셰이크 쿠키에서 userId 추출 → 없으면 connection_refused
  ├─ 쿼리의 slug로 room·participant 조회
  │    · participant 없음 → connection_refused (NOT_A_PARTICIPANT)
  ├─ GETSET user:sock:{userId} = sid
  │    · 이전 sid가 있으면 그 소켓에 session:superseded 발행 후 강제 disconnect
  ├─ sock:{sid} 저장, presence 갱신
  ├─ §13의 Socket.IO room에 가입
  └─ room:joined 발행 (개인)
```

`GETSET`이 원자적이므로 두 Pod에 동시에 붙어도 마지막 하나만 남는다. 이것이 ID-07의 "한 사람이 여러 창으로 중복 제출·득점하는 것"을 막는 실제 장치다.

> 엣지 케이스 표의 "같은 사람이 여러 기기로 중복 입장 → 별개 참여자"와 충돌하지 않는다. **다른 기기·시크릿 모드는 쿠키가 달라 UUID 자체가 다르므로** 별개 사용자이고, ID-07은 **같은 UUID**의 중복 연결만 다룬다.

### 6.3 촬영 세션 토큰 (CP-03)

```python
payload = f"{round_id}:{participant_id}:{deadline_at_ms}"
token   = base64url(hmac_sha256(CAPTURE_SECRET, payload))[:32]
```

- `round:revealed`를 **참여자별 개인 이벤트로** 발행하며 그 안에 담아 내린다. 방 전체 broadcast로는 보내지 않는다.
- 업로드 시 `X-Capture-Token` 헤더로 제출한다. 검증: HMAC 일치 → 경로의 `roundId`·인증된 `participantId`와 payload 일치 → `deadlineAt` 미도과 → `SET round:{roundId}:tok:{pid} 1 NX`로 1회 사용 마킹.
- **한계를 명확히 기록한다.** 이 토큰은 "정상 촬영 세션에서 발급되었는가"만 증명하며, 업로드된 픽셀이 실제로 인앱 프리뷰에서 캡처된 것인지는 증명하지 못한다. PRD CP-03이 요구하는 것도 여기까지다. 파일 선택 UI를 노출하지 않는 것과 합쳐 "우연한 갤러리 제출"을 막는 수준으로 본다.

### 6.4 카메라 권한과 서버의 인지 범위 (PM-07)

**서버는 클라이언트의 카메라 권한 상태를 알 수 없다.** PRD v1.0이 `Participant.cameraPermission`을 폐기했기 때문에 저장할 필드도 없다.

따라서 PM-07의 "대기실 입장 차단"은 다음 계약으로 구현된다.

```
클라이언트: 카메라 스트림 획득 성공 후에만 POST /api/rooms/{slug}/participants 를 호출한다
서버      : 이 API 호출을 "카메라 준비 완료" 신호로 신뢰한다
```

- 이 API를 호출하기 전에는 `participants` 레코드가 없고, 레코드가 없으면 Socket.IO 연결이 거부되므로(§6.2) **방의 어떤 정보도 흘러가지 않는다.** PM-07의 "방 이름·참여 인원·라운드 진행·다른 참여자의 사진·점수 어느 것도 볼 수 있는 경로를 제공하지 않는다"가 성립한다.
- 입장 전 열람 가능한 정보는 `GET /api/rooms/{slug}`가 내려주는 `{exists, status, isFull}` 셋뿐이다. RO-15("방을 찾을 수 없어요")와 RO-06("방이 가득 찼어요")를 안내하기 위한 최소 정보다.
- `permission:changed`(C→S)는 PM-11의 자진 신고 채널이다. 서버는 이를 받으면 참여자를 퇴장 처리한다. 신고하지 않고 그냥 촬영을 못 하는 경우는 일반 미제출로 흡수된다.

---

## 7. 공통 API 규약

### 7.1 응답 형태

성공은 자원 표현을 그대로 반환한다. 실패는 항상 아래 형태다.

```json
{ "error": { "code": "ROOM_FULL", "message": "방이 가득 찼어요", "detail": { "capacity": 12 } } }
```

`code`는 §15의 안정적인 식별자이고, `message`는 그대로 화면에 노출 가능한 한국어다. FE가 `code`로 분기하고 `message`를 표시한다.

### 7.2 필드 규약

| 규약 | 값 |
|---|---|
| 시각 | `...AtMs` 접미사, epoch milliseconds 정수 |
| 식별자 | 방은 `slug`(문자열), 그 외는 정수 id를 문자열로 직렬화 |
| 점수 | `targetScore`는 소수 첫째 자리까지의 실수 (SC-01) |
| 미확정 | `null`. 필드 생략으로 표현하지 않는다 |

### 7.3 멱등성

`POST /api/rooms`(RO-02), `POST .../participants`(재입장), `POST .../submissions`(중복 제출), `reaction:sent`·`round:skip`(토글)은 모두 **재호출이 안전**해야 한다. 네트워크 재시도(CP-05의 자동 1회 재시도 포함)가 상태를 깨뜨리지 않는다.

---

## 8. REST API

### 8.1 세션

#### `GET /api/me`
쿠키가 없으면 UUID를 발급하고 `Set-Cookie`를 내린다 (ID-01).

```json
{ "nickname": "지수", "hasActiveRoom": true, "activeRoomSlug": "Xk9mQ2vB7nLp" }
```

`nickname`은 재접속 시 입력란 기본값으로 쓴다(ID-04). `hasActiveRoom`은 랜딩에서 RO-03 자동 이동을 미리 안내하기 위한 힌트다.

#### `PATCH /api/me`
`{ "nickname": "지수" }` → 2~10자 검증 후 저장 (ID-04).

---

### 8.2 방

#### `POST /api/rooms` — 방 생성 (RO-01·02·03·17)

요청
```json
{ "roundCount": 5, "timeLimitSec": 20 }
```

동작
```
1. 레이트 리밋 검사 (§16: IP 10회/시간, 사용자 5회/시간)
2. INSERT rooms (slug = secrets.token_urlsafe(9))
     └ uq_room_active_owner 위반(23505) → 기존 활성 방 조회 후 200 반환
3. 201 반환
```

경합을 앱 레벨 "먼저 조회 후 삽입"으로 막지 않고 **유니크 인덱스 위반을 잡아 분기한다.** 동시에 두 번 눌러도 방이 두 개 생기지 않는다.

응답 `201` (신규) / `200` (기존, RO-03)
```json
{ "slug": "Xk9mQ2vB7nLp", "status": "waiting", "isHost": true,
  "settings": { "roundCount": 5, "timeLimitSec": 20, "emotionSet": "full" },
  "existing": false }
```

`existing: true`면 FE가 "이미 만든 방으로 이동했어요" 토스트를 띄운다(RO-03).

#### `GET /api/rooms/{slug}` — 입장 전 최소 조회 (RO-06·15, PM-07)

```json
{ "exists": true, "status": "waiting", "isFull": false, "isHost": false,
  "settings": { "roundCount": 5, "timeLimitSec": 20 } }
```

**참여자 목록·닉네임·점수는 절대 포함하지 않는다.** 설정 요약만 내리는 것은 대기실 헤더의 `5라운드 · 20초 · 최대 12명`(DECISIONS C-9)을 위해서인데, 이 값은 입장 전에도 무해하다.
없는 방은 `404 ROOM_NOT_FOUND`, `finished`/`closed`는 `410 ROOM_CLOSED`.

#### `POST /api/rooms/{slug}/participants` — 입장 (RO-05·06·09·16, PM-07)

요청 `{ "nickname": "지수" }`

```
1. 방 상태 검증 → waiting|playing 아니면 410
2. 기존 participant(room_id, user_id) 조회
     · 있고 status=left     → active로 복귀, totalPoints 유지 (RO-16, PM-11)
     · 있고 그 외           → 그대로 반환 (멱등)
     · 없음                 → 정원 검사 후 신규 생성
3. 정원 검사: count(status IN ('active','waiting_next_game')) >= 12 → 409 ROOM_FULL
4. status 결정: room.status == 'playing' ? 'waiting_next_game' : 'active'   (RO-09)
5. colorTag = 방 안에서 사용되지 않은 가장 작은 0..11
```

닉네임 중복은 검사하지 않는다(D-7).

응답 `201`
```json
{ "participantId": "41", "nickname": "지수", "colorTag": 3,
  "status": "active", "isHost": false, "socketPath": "/socket.io" }
```

#### `PATCH /api/rooms/{slug}/settings` — 설정 변경 (RO-17, RD-01·04)

방장만, `status='waiting'`에서만. 아니면 `403 NOT_HOST` / `409 GAME_ALREADY_STARTED`.
`{ "roundCount": 7, "timeLimitSec": 30 }` → 변경 후 `room:settingsUpdated` broadcast.

#### `POST /api/rooms/{slug}/start` — 게임 시작 (RO-08, PM-10)

```
1. require_host
2. status == 'waiting' 확인
3. count(status='active') >= 2 확인 → 아니면 409 NOT_ENOUGH_PLAYERS
4. emotion_sequence 생성 (§9.2) 후 저장
5. status = 'playing', waiting_next_game → active 승격
6. game:started broadcast → 1라운드 시작 (§10.1)
```

#### `POST /api/rooms/{slug}/close` — 방 닫기 (RO-13)

방장만. `status='closed'`, 소유 슬롯 반납, `room:closed{reason:"host_closed"}` broadcast 후 전 소켓 해제.

#### `DELETE /api/rooms/{slug}/participants/me` — 나가기

`status='left'` 전환. 진행 중 라운드가 있으면 해당 라운드는 미제출로 확정된다. 인원이 2명 미만이 되면 게임 종료 판정(§10.6).

#### `GET /api/rooms/{slug}/state` — 재접속 복원 (ID-05, 가이드 15장)

§14 참조. 소켓 연결 전에 화면을 정확한 지점으로 복원하기 위한 단일 진입점이다.

---

### 8.3 제출

#### `POST /api/rooms/{slug}/rounds/{roundId}/submissions` (CP-08·09·10, PV-01)

```
Content-Type: multipart/form-data
X-Capture-Token: <§6.3 토큰>

image: <JPEG blob>
```

Q-7 확정(2026-09-08): 클라이언트는 원본 비율을 유지하고 전체 프레임을 거울 방향으로 한 번 반전한 JPEG를 제출한다. 서버는 결과용 이미지에 추가 반전이나 크롭을 적용하지 않는다. FE는 프리뷰·확인·결과를 3:4 프레임 안에 contain으로 표시하며 여백을 허용한다.

**처리 순서가 곧 요구사항이다.** 가이드 10장의 "HTTP request 도착 → serverReceivedAt 기록 → deadline 비교 → body 처리"를 그대로 따른다.

```
① serverReceivedAt = now_ms()          ← 헤더 수신 직후. body를 읽기 전 (CP-08)
② round 조회, 소속·현재 라운드 검증
③ serverReceivedAt >= round.deadlineAt → 410 DEADLINE_PASSED (CP-10)
④ 촬영 토큰 검증 + 1회 사용 마킹 (CP-03)
⑤ HSETNX round:{id}:submitted {pid} {serverReceivedAt}
      실패 → 409 ALREADY_SUBMITTED (멱등: 기존 결과 상태를 함께 반환)
⑥ 여기서 처음 body를 읽는다. Content-Length > 2MB → 413
⑦ submission:status broadcast (제출 인원 수만, RS-09)
⑧ 전원 제출이면 라운드 즉시 마감 (RD-07)
⑨ 추론 큐 투입 (§11) — 응답은 기다리지 않는다
⑩ 202 Accepted 반환
```

- ⑤가 ③보다 **뒤에** 있는 것이 중요하다. 마감 후 요청은 제출 슬롯을 소비하지 않는다.
- ⑨의 결과는 `submission:scored`로 비동기 전달된다. 가이드 6장(FE)의 "업로드가 시작됐다는 이유로 성공 처리하지 않는다"와 맞물린다.
- 클라이언트가 `clientSubmittedAt` 등을 보내도 무시한다(CP-09, 가이드 10장).

응답 `202`
```json
{ "submissionId": "913", "acceptedAtMs": 1757203355120, "status": "processing" }
```

주요 실패
| 코드 | 상태 | 조건 |
|---|---|---|
| `DEADLINE_PASSED` | 410 | 마감 후 도착 (CP-10) |
| `ALREADY_SUBMITTED` | 409 | 중복 제출 |
| `INVALID_CAPTURE_TOKEN` | 403 | 토큰 불일치·재사용 |
| `NOT_CURRENT_ROUND` | 409 | 지난 라운드로의 제출 |
| `PAYLOAD_TOO_LARGE` | 413 | 2MB 초과 |
| `UNSUPPORTED_MEDIA` | 415 | 디코드 실패 (가이드 26장) |

---

### 8.4 이미지 서빙 (D-1)

#### `GET /media/{mediaToken}`

```python
payload = f"{round_id}:{submission_id}:{viewer_participant_id}:{expires_at_ms}"
media_token = base64url(payload) + "." + base64url(hmac_sha256(MEDIA_SECRET, payload))[:32]
```

- 토큰은 `submission:scored` 페이로드 안에 담아, **결과 열람 권한이 있는 참여자에게 각각 다른 토큰으로** 발급한다(§13). 링크를 유출해도 다른 사람의 `viewerParticipantId`로는 열리지 않는다.
- 서빙 시 검증: 서명 → 만료(라운드 감상 종료 시각 + 10초) → 쿠키의 UUID가 `viewerParticipantId`의 소유자인지 → `img:{roundId}:{pid}` 존재 여부.
- 응답 헤더: `Cache-Control: private, no-store`, `Content-Type: image/jpeg`, `Content-Disposition: inline`. PV-04에 따라 다운로드를 유도하지 않는다.
- 키가 이미 지워졌으면 `410 MEDIA_EXPIRED`. FE는 자리표시자를 그린다.

### 8.5 헬스체크 (가이드 24장)

| 경로 | 성공 조건 |
|---|---|
| `GET /health/live` | 프로세스 응답 |
| `GET /health/ready` | 앱 초기화 + 감정 모델 로드 + FaceDetector 로드 + warmup 완료 + PostgreSQL·Redis 연결 확인 |

`ready`가 통과하기 전에는 트래픽을 받지 않는다. 모델 파일이 없으면 **startup을 실패시킨다**(가이드 19장).

---

## 9. 감정 세트

### 9.1 마스터 테이블

서버가 단일 소스이며, 표시 이름·이모지·설명·색은 `round:revealed`로 내려준다(EM-02). FE에 하드코딩하지 않는다.

| 라벨 | 표시 이름 | 이모지 | 색 | 한 줄 설명 | 난이도 |
|---|---|---|---|---|---|
| `happy` | 기쁨 | 😆 | `#FFD72F` | 좋은 소식을 방금 들은 것처럼 | easy |
| `sad` | 슬픔 | 😢 | `#5AC8FF` | 아끼던 걸 잃어버린 것처럼 | easy |
| `angry` | 분노 | 😠 | `#FF5A47` | 새치기를 당한 것처럼 | easy |
| `surprise` | 놀람 | 😲 | `#8F66FF` | 뒤에서 누가 부른 것처럼 | easy |
| `neutral` | 시크 | 😐 | `#80EFD6` | 아무 일도 없다는 듯이 | easy |
| `disgust` | 우웩 | 🤢 | `#B8F16A` | 상한 우유를 마신 것처럼 | hard |
| `fear` | 무서움 | 😨 | `#FF3D86` | 어두운 복도에서 소리가 난 것처럼 | hard |

모델 출력 인덱스와 라벨의 매핑은 `inference` 레이어가 소유하고, 게임 로직은 라벨 문자열만 다룬다(가이드 5장).

### 9.2 시퀀스 생성 (RD-02, EM-03)

게임 시작 시점에 `roundCount`개를 한 번에 뽑아 `rooms.emotion_sequence`에 저장한다.

```
1. 세트에서 roundCount개를 비복원 추출 (RD-02: 중복 금지)
2. hard(disgust, fear)가 인접하지 않도록 셔플을 최대 20회 재시도 (EM-03)
   · 실패해도 그대로 진행한다. EM-03은 P1이므로 게임을 막지 않는다
3. 저장
```

미리 확정해 두면 무효 라운드(D-5)가 나도 남은 시퀀스를 그대로 이어 쓸 수 있고, 재접속 복원 시 서버가 같은 답을 준다.

`emotionSet='easy'`(5종)는 P1이며, 활성화 시 `roundCount=7`과 양립할 수 없으므로 **설정 API에서 조합을 거부**해야 한다(`409 INVALID_SETTINGS_COMBINATION`). MVP는 `full` 고정이라 이 경로가 열리지 않는다.

---

## 10. 게임 루프

### 10.1 라운드 타임라인

```
T0                    T0+3s                        deadlineAt              F
│                     │                            │                       │
├─ round:revealed ────┼── capturing ───────────────┼── scoring ────────────┤
│  countdownEndsAt    │  촬영·제출                 │  잔여 추론 대기        │  round:finalized
│  deadlineAt         │                            │                       │  viewingEndsAt = F + V
│  captureToken       │  ↳ 제출 즉시 개별 채점      │                       │
│                     │    submission:scored       │                       │
│                     │  ↳ 전원 제출 시 조기 마감   │                       │
                                                                            │
                      F+V (또는 방장 스킵 / 열람자 전원 스킵)               │
                        └─ round:closed → 다음 라운드 또는 game:finished ───┘

deadlineAt = T0 + 3s + timeLimitSec              (RD-03, RD-08)
V          = min(60, 10 + viewerCount × 2) 초     (RS-08)
```

- `T0`는 서버가 라운드 레코드를 만드는 시각이다. `round:revealed`는 절대 시각만 담고, 클라이언트는 남은 시간을 계산해 표시만 한다(RD-08, 7.2).
- `V`의 `viewerCount`는 **해당 라운드 결과 열람 권한자 수**로 정의한다. PRD는 "참여 인원"이라고만 썼는데, RS-15의 스킵 분모와 같은 집합을 쓰는 편이 일관된다. 미제출자가 많은 라운드에서 감상 시간이 불필요하게 길어지지 않는 이점도 있다.

### 10.2 제출 마감

두 경로 중 먼저 오는 쪽이 라운드를 `scoring`으로 넘긴다.

| 경로 | 트리거 |
|---|---|
| 시간 만료 | 스케줄러의 `round_deadline` 잡 (§10.4) |
| 전원 제출 | `HLEN round:{id}:submitted == count(active participants)` (RD-07) |

두 경로가 동시에 발화할 수 있으므로 `lock:room:{roomId}`로 직렬화하고, 라운드 상태를 `capturing → scoring`으로 **한 번만** 전이시킨다(compare-and-set).

마감 시점에 미제출자를 확정한다.

```
미제출자 = active 참여자 − round:{id}:submitted 의 키
  · submissions에 status='missed', rank_points=0 으로 기록 (RD-06)
  · Socket.IO viewers room에 절대 넣지 않는다 (RS-12)
  · round:missed 발행 (D-4 1단계)
```

### 10.3 최종 정렬과 감상 시간

```
scoring 종료 조건: 제출된 모든 건이 확정 상태(submitted|no_face|failed)에 도달
  · 각 추론은 SC-07의 5초 타임아웃이 걸려 있으므로 상한이 존재한다
  · 안전망: 마감 + 8초가 지나면 미확정 건을 강제로 failed 처리한다

→ 제출자 전원이 failed 면 voided (D-5, §10.5)
→ 아니면 §11의 채점 알고리즘 실행
→ 트랜잭션: submissions 확정 + participants.total_points·best_round_score 갱신 (D-8)
→ rounds.status='finalized', finalized_at=F, viewing_ends_at=F+V
→ round:finalized (viewers only) + round:missedUpdate (미제출자, D-4 2단계)
→ 스케줄러에 round_viewing_end 잡 예약
```

감상 종료 트리거는 셋이고, 먼저 오는 쪽이 이긴다(RS-08, RS-15).

| 트리거 | 조건 |
|---|---|
| 시간 만료 | `viewingEndsAt` 도달 |
| 방장 스킵 | 방장의 `round:skip` — 즉시 종료 |
| 전원 스킵 | `|skips| >= |스킵 분모|` |

**스킵 분모** = 해당 라운드 결과 열람 권한자(viewers) 중 `connectionStatus='connected'` 인 참여자.
미제출자는 애초에 viewers가 아니므로 자동 제외된다(RS-15, C-10). 연결이 끊긴 사람을 분모에 남기면 "전원 스킵"이 영원히 성립하지 않아 규칙이 사문화되므로 제외한다. 게임 종료·정원 판정이 `status` 기준(D-6)인 것과는 별개의 판단이며, 스킵은 진행을 앞당기는 편의 기능이라 즉시성을 우선한다.

### 10.4 분산 스케줄러

가이드 23장의 multi-pod 전제에서 타이머가 정확히 한 번 발화해야 한다. Redis keyspace notification은 유실 가능성이 있어 쓰지 않고, **ZSet 폴링 + 원자적 pop**으로 구현한다.

```
예약   ZADD sched:timers {fireAtMs} '{"job":"round_deadline","roundId":"87"}'
발화   각 Pod가 250ms 주기로:
         Lua: ZRANGEBYSCORE sched:timers -inf {now} LIMIT 0 10
              → 각 member에 ZREM. ZREM이 1을 반환한 것만 실행 권한을 얻는다
       추가 안전망으로 SET sched:lock:{jobId} 1 NX EX 30
취소   ZREM sched:timers {member}
```

`ZREM`의 반환값이 소유권을 결정하므로 여러 Pod가 동시에 폴링해도 잡은 한 번만 실행된다. 250ms 주기는 7.2의 "상태 전환 편차 500ms 이내"를 만족한다.

| 잡 | 예약 시점 | 동작 |
|---|---|---|
| `round_deadline` | 라운드 생성 | 제출 마감 (§10.2) |
| `round_scoring_guard` | 마감 시 (+8s) | 미확정 추론 강제 failed |
| `round_viewing_end` | finalized 시 | 라운드 종료 → 다음 라운드 |
| `participant_left` | disconnect 시 (+60s) | `status='left'`, 인원 재판정 (D-6) |
| `host_delegate` | 방장 disconnect 시 (+60s) | 임시 위임 (RO-11) |
| `room_expire` | `lastActiveAt` 갱신마다 갱신 (+30m) | 방 소멸 (RO-14) |

### 10.5 무효 라운드 (D-5)

```
scoring 단계에서 제출자 전원이 failed
  → rounds.status = 'voided'
  → 포인트를 아무에게도 부여하지 않는다 (submissions는 failed 상태로 남김)
  → rooms.consecutive_voided += 1
  → round:voided broadcast (전원. 미제출자에게도 보낸다 — 결과 정보가 없으므로 RS-12 위반이 아니다)
  → consecutive_voided >= 3 → 게임 중단, game:finished{aborted:true, reason:"engine_unavailable"}
  → 아니면 남은 시퀀스로 다음 라운드. 실제 진행 라운드 수는 그만큼 줄어든다
```

정상적으로 `finalized`된 라운드가 하나라도 나오면 `consecutive_voided = 0`으로 초기화한다.
새 라운드 시작 전 추론 러너의 서킷 브레이커 상태를 확인해, 열려 있으면 2초 대기 후 한 번 더 확인한다.

### 10.6 게임 종료

| 조건 | 처리 |
|---|---|
| 마지막 라운드가 `closed` | 정상 종료 |
| `count(status='active') < 2` (D-6 판정 시점) | 즉시 종료, 현재까지 순위 공개 |
| `consecutive_voided >= 3` | 중단 종료 (§10.5) |

```
rooms.status = 'finished'          ← 소유 슬롯 즉시 반납 (RO-13, FN-01, C-12)
current_round_id = NULL
game:finished 발행 (미제출 페널티를 받았던 참여자에게도 전원 발행 — FN-01)
잔여 img:* 키 전량 DEL
```

### 10.7 방장 부재 (RO-10·11·12)

```
방장 소켓 disconnect
  ├─ host_delegate 잡 예약 (+60초). 게임은 그대로 진행된다 (RO-10)
  ├─ 60초 안에 같은 UUID로 재접속 → 잡 취소, 권한 유지
  └─ 60초 경과
       ├─ 남은 active 참여자 중 joined_at 최소인 사람을 임시 방장으로 (RO-11)
       ├─ rooms.host_user_id는 바꾸지 않는다 ← RO-11의 "권한을 되돌려준다"의 근거
       ├─ 임시 방장은 Redis room:{id}:tempHost 에 둔다
       └─ host:changed{ hostParticipantId, temporary: true } broadcast

원래 방장 재접속 (rooms.host_user_id 일치)
  → tempHost 삭제, host:changed{ temporary: false } broadcast
임시 방장마저 이탈
  → 다음 순번으로 연쇄 위임
```

`isHost` 판정은 항상 `rooms.host_user_id == user.uuid || tempHost == participantId`이며, 클라이언트가 보낸 값은 쓰지 않는다(가이드 10장 `clientHost`).

RO-12(쿠키 삭제 후 재입장)는 별도 처리가 필요 없다. 새 UUID이므로 `host_user_id`와 일치하지 않아 자연히 일반 참여자가 되고, FE가 `isHost:false`를 보고 안내 문구를 띄운다.

---

## 11. 채점

`domain/scoring`의 순수 함수로 구현한다. 입력은 제출 목록, 출력은 순위·포인트다. I/O가 없으므로 §18의 테이블 테스트로 전 분기를 검증할 수 있다.

### 11.1 라운드 점수 (SC-01)

```python
target_score = round(probabilities[target_emotion] * 100, 1)   # 0.0 ~ 100.0
```

### 11.2 상태별 처리

| 상태 | 조건 | 정렬 참여 | `targetScore` | `rankPoints` |
|---|---|---|---|---|
| `submitted` | 정상 채점 | O | 실제 값 | 순위별 100/70/50/30 |
| `no_face` | 얼굴 미검출 (SC-04) | O (0.0으로 최하위권) | `0.0` | **30 고정** (D-2) |
| `failed` | 엔진 오류·타임아웃 (SC-05) | **X** | `null` | **정상 채점자 평균의 반올림** (D-2) |
| `missed` | 미제출 (RD-06) | X | `null` | `0` |

**`no_face`의 30점 고정을 강조한다.** 정렬에는 참여해 레일 최하위에 표시되지만, 매겨진 순위가 2위여도 포인트는 30이다(2인 방에서 발생 가능). "제출은 했다"는 사실만 인정하고 성적은 인정하지 않는다는 D-2의 의도를 그대로 옮긴 것이다.

### 11.3 정렬과 포인트 (SC-02·03·09)

```python
RANK_POINTS = {1: 100, 2: 70, 3: 50}
DEFAULT_POINTS = 30

def finalize(subs):
    ranked = sorted(
        [s for s in subs if s.status in ("submitted", "no_face")],
        key=lambda s: (-s.target_score, s.received_at_ms),   # SC-03: 동점은 먼저 제출한 쪽 상위
    )
    for i, s in enumerate(ranked, start=1):
        s.rank = i
        s.rank_points = 30 if s.status == "no_face" else RANK_POINTS.get(i, DEFAULT_POINTS)

    scored = [s.rank_points for s in ranked]
    avg = round(sum(scored) / len(scored)) if scored else DEFAULT_POINTS   # D-2

    for s in subs:
        if s.status == "failed":
            s.rank, s.rank_points = None, avg      # SC-05: 페널티 없음
        elif s.status == "missed":
            s.rank, s.rank_points = None, 0        # RD-06
```

- 동점 비교의 `received_at_ms`는 서버 수신 시각이다(CP-08·09).
- 포인트는 **최종 정렬이 확정된 시점에만** 계산한다(SC-09). `submission:scored`가 실어 보내는 `currentRank`는 표시용이며 포인트를 담지 않는다.
- 리액션은 어떤 경로로도 점수에 들어가지 않는다(SC-06, RX-01).

### 11.4 누적과 최종 순위 (SC-06)

```
participant.total_points     += rank_points
participant.best_round_score  = max(best_round_score, target_score or 0)

최종 순위 정렬 키: (-total_points, -best_round_score, joined_at)
```

3차 키 `joined_at`은 PRD에 없지만 결정론적 출력을 위해 필요하다. 완전 동점이면 먼저 입장한 쪽이 위다.

**"가장 사랑받은 표정"** (RX-11) = 게임 전체 누적 `like_count`가 최대인 참여자. 동수면 누적 `question_count`가 적은 쪽, 그래도 같으면 `joined_at`이 이른 쪽. 리액션이 0인 게임에서는 수상자를 두지 않는다(`null`).

---

## 12. Inference

### 12.1 경계 (가이드 5장)

```python
class EmotionResult(BaseModel):
    face_detected: bool
    probabilities: dict[EmotionLabel, float] | None   # 합 1.0, face_detected=False면 None
    face_box: tuple[int, int, int, int] | None        # 로깅 금지 (PV-05)

class InferenceError(Exception): ...      # 처리 실패. NO_FACE와 구분 (PRD 5.7 계약)

class EmotionClassifier(Protocol):
    async def classify(self, image: bytes) -> EmotionResult: ...
```

`domain` 레이어는 `torch`·`mediapipe`를 import하지 않는다. 테스트에서는 `FakeClassifier`를 주입해 얼굴 미검출·실패·타임아웃·특정 확률 분포를 결정론적으로 재현한다.

### 12.2 파이프라인 (가이드 6·7장)

```
JPEG bytes
  → Pillow decode (실패 시 415 UNSUPPORTED_MEDIA)
  → MediaPipe FaceDetector
       · 검출 0개 → EmotionResult(face_detected=False)  → NO_FACE  (가이드 7장)
       · 검출 N개 → bbox width×height 최대인 하나 선택   (10장 엣지 케이스)
  → Face crop
  → resize 224×224
  → RGB → BGR
  → VGGFace2 mean subtraction
  → ResNet50
  → Softmax → 7 확률
```

`/255` 정규화와 ImageNet mean/std를 **추가하지 않는다**(가이드 6장). preprocessing은 모델 버전의 일부로 취급하며, 모델 교체 시 함께 버전을 올린다(가이드 21장).

### 12.3 동시성과 타임아웃 (SC-07, 가이드 25장)

```
submission → asyncio.Queue → InferenceRunner
                                 ├─ asyncio.Semaphore(INFERENCE_CONCURRENCY)
                                 ├─ 블로킹 연산은 전용 ThreadPoolExecutor에서 실행
                                 └─ asyncio.wait_for(timeout=5.0)   ← SC-07
```

- `INFERENCE_CONCURRENCY` 기본값 **2**. 가이드 25장이 요구하는 "benchmark로 결정"의 초기값이며, M1에서 측정해 조정한다. 임의로 높이지 않는다.
- 모델 연산이 이벤트 루프를 막으면 Socket.IO 하트비트가 밀려 7.2의 500ms 목표가 깨진다. 반드시 executor로 내보낸다(가이드 31장 7번).
- 타임아웃·예외는 모두 `failed`(SC-05)이고, 검출 실패만 `no_face`(SC-04)다. **둘을 절대 뭉뚱그리지 않는다**(가이드 30장).
- 서킷 브레이커: 최근 20건 중 실패가 80% 이상이면 60초 동안 열어 두고, 그동안의 제출은 즉시 `failed`로 확정한다. 5초 타임아웃을 12명이 줄줄이 기다리는 상황을 막는다.

### 12.4 모델 아티팩트 (가이드 18~21장)

```
/models/
├── emotion/v1/FER_static_ResNet50_AffectNet.pt
└── face/v1/blaze_face_short_range.tflite
```

Pod startup에서 로드 → FaceDetector 로드 → 더미 이미지로 warmup 1회 → `ready`. 파일이 없으면 startup 실패(가이드 19장). 애플리케이션은 모델을 다운로드하지 않는다.

> **확인 필요** — 저장소의 `models/`에는 텍스트 감정 분류 모델(`sentiment-analysis-fine-tuned-model`, kor_unsmile 기반)만 있고 얼굴 표정 모델 아티팩트가 없다. PoC인 `femo_web.py`가 import하는 `femo` 모듈(`LABELS`, `build_model`, `detect_and_crop_face`, `preprocess`)도 저장소에 없다. 위 경로·파일명은 가이드 19장의 예시를 그대로 옮긴 것이므로, 실제 아티팩트 확보 후 확정해야 한다(§21-A).

---

## 13. 실시간 계층

### 13.1 Socket.IO room 구조

RS-12와 가이드 13장은 "미제출자에게 이벤트를 **발행하지 않는다**"를 요구한다. 클라이언트에서 가리는 구현은 금지다. room을 나눠 서버에서 물리적으로 차단한다.

| room | 구성원 | 받는 이벤트 |
|---|---|---|
| `r:{roomId}` | 그 방의 모든 소켓 (`waiting_next_game` 포함) | `room:*`, `host:changed`, `participant:*`, `game:started`, `game:finished`, `round:voided` |
| `r:{roomId}:players` | `status='active'` | `round:revealed`(개인 발행이라 실제로는 미사용), `submission:status` |
| `rd:{roundId}:viewers` | **그 라운드에 제출이 인정된 참여자만** | `submission:scored`, `round:finalized`, `reaction:updated`, `round:skipStatus`, `round:closed` |
| 개인 sid | 각 소켓 | `room:joined`, `state:restored`, `round:revealed`(촬영 토큰 포함), `round:missed`, `round:missedUpdate`, `session:superseded`, `error` |

**`rd:{roundId}:viewers` 가입 시점은 제출이 인정되는 순간**(§8.3 ⑤ 성공 직후)이다. 마감 시점에 미제출인 참여자는 이 room에 들어간 적이 없으므로, 결과 URL로 직접 접근해도 서버가 아무것도 보내지 않는다(10장 엣지 케이스).

발행은 전부 `realtime/emitter.py`를 통과시킨다. 개별 핸들러가 `sio.emit`을 직접 부르지 못하게 하고, emitter가 "이 이벤트가 이 room으로 나가도 되는가"를 한 곳에서 검사한다(가이드 12장 Authorization).

### 13.2 이벤트 명세

PRD 9장의 이벤트 목록을 페이로드 수준으로 확정한다. `+`는 이 문서에서 새로 추가한 것이다.

#### S→C

**`room:joined`** (개인)
```json
{ "room": { "slug": "Xk9mQ2vB7nLp", "status": "waiting",
            "settings": { "roundCount": 5, "timeLimitSec": 20, "emotionSet": "full" } },
  "me": { "participantId": "41", "isHost": true, "status": "active", "totalPoints": 0 },
  "participants": [ { "participantId": "41", "nickname": "지수", "colorTag": 3,
                      "connectionStatus": "connected", "status": "active", "isHost": true } ],
  "game": null }
```
`game`은 진행 중이면 §14의 `game` 블록과 같은 형태다. UUID는 어디에도 없다(ID-08).

**`room:settingsUpdated`** `+` (`r:{roomId}`)
```json
{ "settings": { "roundCount": 7, "timeLimitSec": 30 } }
```

**`participant:updated`** (`r:{roomId}`)
```json
{ "participantId": "44", "connectionStatus": "disconnected", "status": "active" }
```

**`participant:removed`** (`r:{roomId}`)
```json
{ "participantId": "44", "reason": "left" }
```
`reason`: `left` | `permission_revoked` | `timeout`

**`host:changed`** (`r:{roomId}`)
```json
{ "hostParticipantId": "42", "temporary": true }
```

**`room:closed`** (`r:{roomId}`)
```json
{ "reason": "host_closed" }
```
`reason`: `host_closed` | `expired`

**`game:started`** (`r:{roomId}`)
```json
{ "roundCount": 5, "timeLimitSec": 20, "participantIds": ["41","42","44"] }
```

**`round:revealed`** (개인 — 촬영 토큰이 참여자마다 다르다)
```json
{ "roundId": "87", "index": 3, "roundCount": 5,
  "emotion": { "label": "surprise", "displayName": "놀람", "emoji": "😲",
               "color": "#8F66FF", "hint": "뒤에서 누가 부른 것처럼" },
  "countdownEndsAtMs": 1757203340000,
  "deadlineAtMs":      1757203360000,
  "captureToken": "b3Rr...",
  "activeCount": 5 }
```
`activeCount`는 RD-09의 제출 현황 분모다.

**`submission:status`** (`r:{roomId}:players`)
```json
{ "roundId": "87", "submitted": 3, "total": 5 }
```
점수는 절대 싣지 않는다 (RS-09).

**`submission:scored`** (`rd:{roundId}:viewers`, 수신자마다 `mediaToken`이 다름)
```json
{ "roundId": "87", "submissionId": "913", "participantId": "42",
  "nickname": "태호", "colorTag": 5,
  "status": "submitted",
  "targetScore": 84.2,
  "topEmotions": [ { "label": "surprise", "score": 84.2 },
                   { "label": "fear", "score": 9.1 },
                   { "label": "happy", "score": 3.4 } ],
  "currentRank": 2,
  "mediaToken": "eyJ...abc",
  "scoredCount": 3, "scoredTotal": 5 }
```
`status`가 `no_face`면 `targetScore: 0.0`, `topEmotions: null`. `failed`면 둘 다 `null`이고 FE가 "판정 불가"로 그린다. **어느 경우에도 `mediaToken`은 발급된다** — 사진이 존재하므로 리액션 대상이다(RX-08).
`currentRank`는 표시용이며 포인트를 담지 않는다(SC-09).

**`round:finalized`** (`rd:{roundId}:viewers`)
```json
{ "roundId": "87",
  "results": [ { "participantId": "42", "rank": 1, "targetScore": 91.3,
                 "rankPoints": 100, "totalPoints": 270, "status": "submitted" },
               { "participantId": "44", "rank": null, "rankPoints": 0,
                 "status": "missed" } ],
  "viewingEndsAtMs": 1757203380000 }
```

**`round:missed`** (개인, 미제출자 — D-4 1단계)
```json
{ "roundId": "87", "index": 3, "roundCount": 5, "phase": "scoring" }
```

**`round:missedUpdate`** `+` (개인, 미제출자 — D-4 2단계)
```json
{ "roundId": "87", "phase": "viewing", "nextRoundAtMs": 1757203380000 }
```
RS-14에 따라 남은 시간 외에는 아무것도 담지 않는다. 다른 참여자의 점수·순위는 물론 참여자 수도 넣지 않는다.

**`reaction:updated`** (`rd:{roundId}:viewers`)
```json
{ "roundId": "87", "submissionId": "913", "like": 4, "question": 1 }
```
누가 눌렀는지는 담지 않는다 (RX-09).

**`round:skipStatus`** (`rd:{roundId}:viewers`)
```json
{ "roundId": "87", "skipped": 3, "total": 5 }
```

**`round:closed`** (`rd:{roundId}:viewers`)
```json
{ "roundId": "87",
  "reactions": [ { "submissionId": "913", "like": 4, "question": 1 } ],
  "results":   [ { "participantId": "42", "rankPoints": 100, "totalPoints": 270 } ],
  "nextRoundAtMs": 1757203381000 }
```
RX-10의 리액션 확정 스냅샷이다. 이 이벤트 이후의 `reaction:sent`는 거부한다. `results`는 `round:finalized`와 같은 값을 재전송하는 멱등 필드로, 중간에 재접속한 참여자가 한 번에 따라잡을 수 있게 한다.

**`round:voided`** `+` (`r:{roomId}`)
```json
{ "roundId": "87", "index": 3, "reason": "engine_unavailable", "nextRoundAtMs": 1757203365000 }
```
결과 정보가 없으므로 미제출자를 포함한 전원에게 보낸다 (D-5).

**`game:finished`** (`r:{roomId}` — FN-01에 따라 페널티 대상 포함 전원)
```json
{ "aborted": false, "reason": null,
  "ranking": [ { "rank": 1, "participantId": "42", "nickname": "태호",
                 "colorTag": 5, "totalPoints": 380,
                 "likeCount": 7, "questionCount": 2 } ],
  "mostLoved": { "participantId": "41", "likeCount": 9 } }
```
`aborted: true`일 때 `reason`은 `engine_unavailable` | `not_enough_players`. `mostLoved`는 수상자가 없으면 `null` (RX-11).

**`session:superseded`** `+` (개인)
```json
{ "reason": "another_connection" }
```
ID-07로 밀려난 소켓에 보내고 즉시 끊는다.

**`error`** `+` (개인)
```json
{ "code": "NOT_A_VIEWER", "message": "이번 라운드 결과는 볼 수 없어요" }
```

#### C→S

모든 C→S 이벤트는 ack 콜백으로 `{ok: true}` 또는 `{ok: false, error: {...}}`를 돌려준다.

**`reaction:sent`** (RX-01~08)
```json
{ "submissionId": "913", "type": "like" }
```
검증 순서
```
1. 발신자가 rd:{roundId}:viewers 인가          → 아니면 NOT_A_VIEWER (RX-07)
2. 라운드가 finalized 이전이거나 감상 중인가    → closed 이후면 REACTION_CLOSED (RX-10)
3. 대상 제출이 채점 확정되었는가                → 아니면 NOT_SCORED_YET (RX-06)
4. 대상이 자기 자신이 아닌가                    → 자기면 SELF_REACTION (RX-05)
5. SADD round:{id}:rx:actors "{sid}:{aid}:{type}"
      1 반환 → 추가, HINCRBY +1
      0 반환 → SREM 후 HINCRBY -1  (RX-04 토글)
6. reaction:updated broadcast (RX-09)
```
`no_face`·`failed` 카드도 3번을 통과한다. "확정"은 성공을 뜻하지 않고 상태가 정해졌음을 뜻한다(RX-08).

**`round:skip`** (RS-15)
```json
{ "roundId": "87" }
```
`SADD`/`SREM` 토글 후 `round:skipStatus` 발행. 방장이 눌렀으면 분모와 무관하게 즉시 라운드를 종료한다(RS-08).

**`permission:changed`** (PM-11)
```json
{ "state": "denied" }
```
진행 중 라운드를 미제출 확정하고 `status='left'`로 내린 뒤 `participant:removed{reason:"permission_revoked"}`를 broadcast한다. `total_points`는 보존한다.

**`presence:ping`** `+` — 25초 주기. `room:{roomId}:presence`와 `rooms.last_active_at` 갱신.

---

## 14. 재접속 복원 (ID-05, 가이드 15장)

`GET /api/rooms/{slug}/state` 하나로 화면을 정확한 지점으로 되돌린다. 소켓 연결 전에 호출한다.

```json
{
  "room": { "slug": "Xk9mQ2vB7nLp", "status": "playing",
            "settings": { "roundCount": 5, "timeLimitSec": 20 } },
  "me": { "participantId": "41", "isHost": false, "status": "active",
          "totalPoints": 170, "nickname": "지수" },
  "participants": [ "..." ],
  "game": {
    "currentRound": {
      "roundId": "87", "index": 3,
      "emotion": { "label": "surprise", "displayName": "놀람", "emoji": "😲",
                   "color": "#8F66FF", "hint": "뒤에서 누가 부른 것처럼" },
      "status": "capturing",
      "countdownEndsAtMs": 1757203340000,
      "deadlineAtMs": 1757203360000,
      "viewingEndsAtMs": null,
      "captureToken": "b3Rr...",
      "mySubmission": { "status": "none" }
    },
    "screen": "capture"
  },
  "serverTimeMs": 1757203352410
}
```

`serverTimeMs`로 클라이언트 시계 오차를 보정한다. 이것 없이는 기기 시계가 틀린 사용자의 타이머가 어긋난다.

**`screen` 결정 규칙** — 서버가 화면을 지정해 FE의 분기 로직을 없앤다.

| 방/라운드 상태 | 내 제출 상태 | `screen` |
|---|---|---|
| `waiting` | — | `lobby` |
| `playing`, 내 status=`waiting_next_game` | — | `lobby_waiting_next` (RO-09) |
| `revealed` | — | `countdown` |
| `capturing` | 미제출 | `capture` |
| `capturing` | 제출됨 | `result` |
| `scoring` / `finalized` | 제출됨 | `result` |
| `scoring` / `finalized` | 미제출 | `round_missed` (RS-11) |
| `voided` | — | `countdown` (다음 라운드 대기) |
| `finished` | — | `final` |
| `closed` | — | `error_room_closed` |

- `screen='round_missed'`면 `game.currentRound`에서 **감정 정보까지 제거**한다. RS-14는 "이번 라운드를 놓쳤다는 사실과 다음 라운드까지 남은 시간만" 표시하도록 요구한다.
- `screen='result'`인 경우에만 이미 확정된 `submission:scored` 목록을 함께 내려 캐러셀·레일을 복원한다.
- 복원 후 소켓이 붙으면 §13.1의 room 재가입이 일어난다. 제출이 인정되었던 참여자는 `rd:{roundId}:viewers`에 다시 들어간다.

7.2의 "10초 이내 재접속 시 진행 중인 라운드 복귀"는 이 경로로 만족된다. `status='left'`로 내려가는 유예가 60초(D-6)이므로 10초 재접속은 항상 같은 참여자 레코드로 복귀한다.

---

## 15. 에러 코드

| 코드 | HTTP | 사용자 문구 | 요구사항 |
|---|---|---|---|
| `ROOM_NOT_FOUND` | 404 | 방을 찾을 수 없어요 | RO-15 |
| `ROOM_CLOSED` | 410 | 종료된 방이에요 | RO-15 |
| `ROOM_FINISHED` | 410 | 이미 끝난 게임이에요 | RO-13 |
| `ROOM_FULL` | 409 | 방이 가득 찼어요 | RO-06 |
| `NOT_HOST` | 403 | 방장만 할 수 있어요 | RO-08, ID-03 |
| `NOT_A_PARTICIPANT` | 403 | 먼저 입장해 주세요 | PM-07 |
| `NOT_ENOUGH_PLAYERS` | 409 | 2명 이상이어야 시작할 수 있어요 | PM-10 |
| `GAME_ALREADY_STARTED` | 409 | 게임이 이미 시작됐어요 | RO-17 |
| `INVALID_SETTINGS_COMBINATION` | 409 | 이 감정 세트로는 그 라운드 수를 고를 수 없어요 | EM-04 (P1) |
| `INVALID_NICKNAME` | 422 | 닉네임은 2~10자로 입력해 주세요 | RO-05 |
| `DEADLINE_PASSED` | 410 | 제출 시간이 끝났어요 | CP-10 |
| `ALREADY_SUBMITTED` | 409 | 이미 제출했어요 | 가이드 26장 |
| `NOT_CURRENT_ROUND` | 409 | 지난 라운드예요 | 가이드 26장 |
| `INVALID_CAPTURE_TOKEN` | 403 | 다시 촬영해 주세요 | CP-03 |
| `PAYLOAD_TOO_LARGE` | 413 | 사진이 너무 커요 | 가이드 26장 |
| `UNSUPPORTED_MEDIA` | 415 | 사진을 읽을 수 없어요 | 가이드 26장 |
| `MEDIA_EXPIRED` | 410 | — (FE가 자리표시자) | PV-02 |
| `NOT_A_VIEWER` | — (소켓) | 이번 라운드 결과는 볼 수 없어요 | RS-11, RX-07 |
| `NOT_SCORED_YET` | — (소켓) | 아직 채점 중이에요 | RX-06 |
| `SELF_REACTION` | — (소켓) | 내 사진에는 누를 수 없어요 | RX-05 |
| `REACTION_CLOSED` | — (소켓) | 리액션이 마감됐어요 | RX-10 |
| `RATE_LIMITED` | 429 | 잠시 후 다시 시도해 주세요 | 7.4 |

`DEADLINE_PASSED`를 받은 FE는 **재시도를 유도하지 않는다**(CP-10). 미제출 안내 화면으로 보낸다.

---

## 16. 레이트 리밋 (7.4, 가이드 26장)

Redis 토큰 버킷. 초과 시 `429 RATE_LIMITED` + `Retry-After`.

| 대상 | 키 | 한도 | 목적 |
|---|---|---|---|
| 방 생성 | IP | 10 / 시간 | 대량 방 생성 방지 |
| 방 생성 | userId | 5 / 시간 | 동일 |
| 방 조회 `GET /api/rooms/{slug}` | IP | 30 / 분 | **slug 무차별 대입 방지** |
| 입장 | IP | 20 / 분 | 동일 |
| 업로드 | participantId | 5 / 라운드 | 과도한 업로드 |
| 소켓 이벤트 | sid | 30 / 10초 | 리액션·스킵 연타 |

방 조회 리밋이 가장 중요하다. DECISIONS C-8의 계산대로 12자 slug(`token_urlsafe(9)` = 72비트)는 사실상 추측 불가지만, 리밋이 없으면 열거 시도 자체가 서버 부하가 된다.

---

## 17. 로깅과 관측성 (PV-05, 가이드 9장)

**절대 로그에 남기지 않는다** — 이미지 원본, base64 이미지, 얼굴 crop, `face_box` 좌표, 픽셀에서 파생된 어떤 값도. 로거에 이 필드들을 차단하는 필터를 두고, 필터 자체를 테스트한다.

**남기는 것** — 구조화 JSON 로그에 `roomId`, `roundId`, `participantId`, 이벤트명, 소요 시간. **`userId`(UUID)는 로그에도 남기지 않는다**(ID-08).

메트릭
```
inference_duration_seconds        histogram   (p95 3초 목표, 7.1)
inference_result_total            counter     {result: ok|no_face|failed|timeout}
round_finalize_duration_seconds   histogram
submission_accept_total           counter     {result: accepted|deadline_passed|duplicate}
socket_connections                gauge
scheduler_job_lag_seconds         histogram   (500ms 목표, 7.2)
redis_image_cache_bytes           gauge
```

`scheduler_job_lag_seconds`가 500ms를 넘기 시작하면 7.2의 동기화 목표가 깨지고 있다는 신호다.

---

## 18. 테스트

가이드 27장의 목록을 이 문서의 결정에 맞춰 확장했다. "게임 규칙은 UI 테스트보다 Backend unit/integration test에서 우선 검증한다."

### 18.1 단위 — `domain/scoring` (순수 함수)

| 케이스 | 기대 |
|---|---|
| 정상 5인 | 100/70/50/30/30 |
| 동점 2인 | 먼저 제출한 쪽이 상위 (SC-03) |
| `no_face` 포함 | 최하위권 정렬 + 포인트 30 고정 (D-2) |
| 2인 방에서 1명 `no_face` | 순위 2위지만 포인트 30 |
| `failed` 1명 | 정렬 제외, 정상자 평균 포인트 (D-2) |
| 제출자 전원 `failed` | `voided` 판정 (D-5) |
| `missed` | 순위 없음, 0포인트 |
| 최종 동점 | `best_round_score` → `joined_at` 순 (SC-06) |
| `mostLoved` 동수 | `question_count` 적은 쪽 (RX-11) |
| 리액션이 점수에 미영향 | 리액션 수를 바꿔도 순위 불변 (SC-06, RX-01) |

### 18.2 통합 — 규칙

| 케이스 | 검증 |
|---|---|
| UUID 사용자 복원 | ID-05 |
| 1인 1 활성 방 | 동시 2회 `POST /api/rooms` → 방 1개 (RO-02) |
| 게임 종료 후 새 방 | 슬롯 반납 확인 (RO-13, FN-01) |
| 쿠키 삭제 후 재입장 | 일반 참여자로 강등 (RO-12) |
| 방장 reconnect | 60초 내 복귀 시 권한 유지 (RO-10) |
| 방장 60초 이탈 | 최초 입장자에게 임시 위임 (RO-11) |
| 임시 방장 후 원 방장 복귀 | 권한 반환 (RO-11) |
| 라운드 deadline | 절대 시각 준수 (RD-08) |
| 마감 1ms 전 업로드 | 인정 (CP-08) |
| 마감 1ms 후 업로드 | `DEADLINE_PASSED` (CP-10) |
| 느린 업로드 | 헤더 도착이 마감 전이면 body 완료가 후여도 인정 (CP-08) |
| 중복 제출 | `ALREADY_SUBMITTED` |
| 촬영 토큰 없음·재사용 | 거부 (CP-03) |
| 전원 제출 시 조기 마감 | RD-07 |
| 얼굴 미검출 / 다중 얼굴 | 가장 큰 얼굴 선택 (10장) |
| 추론 실패 / 타임아웃 | `no_face`와 구분 (SC-04·05) |
| **미제출자 결과 차단** | `rd:*:viewers` 미가입 → 5개 이벤트 미수신 (RS-12) |
| 미제출자 2단계 알림 | `round:missed` → `round:missedUpdate` (D-4) |
| 리액션 사진당 종류별 1회 | RX-02·03 |
| 리액션 토글 취소 | RX-04 |
| 자기 사진 리액션 | 거부 (RX-05) |
| `no_face`·`failed` 카드 리액션 | 허용 (RX-08) |
| 라운드 종료 후 리액션 | 거부 (RX-10) |
| 미제출자 스킵 분모 제외 | 전원 스킵 성립 (RS-15) |
| 방장 부재 중 전원 스킵 | 진행 (C-10) |
| 인원 2명 미만 | 게임 종료 (10장) |
| 일시 disconnect 10초 | 게임 유지, 복원 성공 (7.2, D-6) |
| 60초 disconnect | `left` 전환, 정원 반납 (D-6) |
| 동일 UUID 2탭 | 이전 소켓 `session:superseded` (ID-07) |
| 이미지 즉시 삭제 | 라운드 종료 후 `img:*` 부재, `/media/*` 410 (PV-01·02) |
| 이미지 토큰 타인 사용 | 403 (D-1) |
| 3회 연속 무효 | 게임 중단 + 현재 순위 공개 (D-5) |

### 18.3 다중 Pod

`ready` 2개 인스턴스를 띄우고 참여자를 나눠 붙인 뒤 검증한다.

- 다른 Pod의 참여자에게 `submission:scored`가 도달하는가 (가이드 23장)
- 스케줄러 잡이 **정확히 한 번** 발화하는가 (§10.4)
- 다른 Pod가 업로드받은 이미지를 `/media/*`로 열 수 있는가 (D-1)
- Pod 재시작 후 진행 중인 게임이 이어지는가 (가이드 30장)

---

## 19. 요구사항 추적표 (P0)

| 요구사항 | 구현 위치 |
|---|---|
| ID-01·02·06 | §6.1 쿠키 미들웨어 |
| ID-03 | §6.1, §10.7 `require_host` |
| ID-04 | §8.1 `PATCH /api/me` |
| ID-05 | §14 `GET .../state` |
| ID-07 | §6.2 `user:sock:{userId}` |
| ID-08 | §13.2 페이로드 전반, §17 로깅 |
| RO-01 | §8.2 `POST /api/rooms`, §4.2 `ck_slug_len` |
| RO-02·03 | §4.2 `uq_room_active_owner`, §8.2 |
| RO-04 | §10.7 |
| RO-05 | §8.2 입장 |
| RO-06 | §3.2 정원 계산, §8.2 |
| RO-07 | §3.2 `connectionStatus`, `participant:updated` |
| RO-08 | §8.2 start |
| RO-09 | §3.2 `waiting_next_game`, §14 `lobby_waiting_next` |
| RO-10·11·12 | §10.7 |
| RO-13 | §3.1, §10.6 |
| RO-14 | §10.4 `room_expire` |
| RO-15 | §8.2 조회, §15 |
| RO-17 | §8.2 settings |
| RD-01·04 | §4.2 CHECK 제약, §8.2 settings |
| RD-02 | §9.2 비복원 추출 |
| RD-03·08 | §10.1 타임라인 |
| RD-05 | §8.3 ⑨, `submission:scored` 개별 발행 |
| RD-06 | §10.2 미제출 확정 |
| RD-07 | §10.2 전원 제출 마감 |
| RD-09 | `submission:status`, `round:revealed.activeCount` |
| RD-10 | 자동 제출·건너뛰기 엔드포인트 없음 |
| EM-01·02 | §9.1, `round:revealed.emotion` |
| PM-07·08 | §6.4, §13.1 room 분리 |
| PM-11 | §13.2 `permission:changed` |
| PM-14 | §2.1 전 구간 TLS |
| CP-03 | §6.3 촬영 토큰 |
| CP-08·09·10 | §8.3 처리 순서 |
| SC-01 | §11.1 |
| SC-02·03·09 | §11.3 |
| SC-04·05 | §11.2 (D-2) |
| SC-06 | §11.4 |
| SC-07 | §12.3 |
| RS-01 | §8.3 202 + 즉시 viewers 가입 |
| RS-02·03·04·05 | `submission:scored` 페이로드 |
| RS-06 | §10.2 미제출 확정 |
| RS-07 | §10.3 `round:finalized` |
| RS-08 | §10.1 `V` 계산, §10.3 |
| RS-09 | `submission:status`가 점수 미포함 |
| RS-11·12·13·14 | §13.1 `rd:{roundId}:viewers`, §14 `round_missed` |
| RS-15 | §10.3 스킵 분모, `round:skip` |
| RX-01~11 | §13.2 `reaction:sent`, §4.2 `uq_reaction`, §11.4 `mostLoved` |
| FN-01·02·03 | §10.6 `game:finished` |
| PV-01·02 | §12 이미지 수명주기, §8.4 |
| PV-05 | §17 |
| 7.4 보안 | §6, §16 |

---

## 20. 환경 변수

```
DATABASE_URL                 postgresql+asyncpg://...
REDIS_URL                    redis://...          # 조율용 (noeviction)
REDIS_MEDIA_URL              redis://.../1        # 이미지 캐시 (allkeys-lru 허용)

COOKIE_SECRET                # §6.1 UUID 서명
COOKIE_SECRET_PREVIOUS       # 로테이션 30일 창
CAPTURE_TOKEN_SECRET         # §6.3
MEDIA_TOKEN_SECRET           # §8.4

EMOTION_MODEL_VERSION        v1
EMOTION_MODEL_PATH           /models/emotion/v1/FER_static_ResNet50_AffectNet.pt
FACE_MODEL_VERSION           v1
FACE_MODEL_PATH              /models/face/v1/blaze_face_short_range.tflite
INFERENCE_CONCURRENCY        2        # §12.3. benchmark로 조정
INFERENCE_TIMEOUT_SEC        5.0      # SC-07

MAX_UPLOAD_BYTES             2097152  # 2MB
HOST_GRACE_SEC               60       # RO-10
PARTICIPANT_GRACE_SEC        60       # D-6
ROOM_IDLE_EXPIRE_SEC         1800     # RO-14
SCHEDULER_TICK_MS            250      # §10.4
```

---

## 21. 남은 확인 사항

구현을 막지는 않지만 확정이 필요한 항목이다. 각 항목에 대해 이 문서가 채택한 잠정 값을 함께 적는다.

| # | 항목 | 잠정 값 | 확정 필요 시점 |
|---|---|---|---|
| **A** | **얼굴 감정 모델 아티팩트가 저장소에 없다.** `models/`에는 한국어 텍스트 감정 모델만 있고, PoC `femo_web.py`가 import하는 `femo` 모듈도 없다 | 가이드 19장의 파일명·경로를 그대로 사용 | **M1 이전.** 실제 모델과 preprocessing 확정 없이는 §12.2를 구현할 수 없다 |
| B | 감상 시간 `V`의 "참여 인원" 정의 | 결과 열람 권한자 수 (§10.1) | M3 |
| C | 스킵 분모에서 `disconnected` 제외 여부 | 제외 (§10.3) | M3 |
| D | `INFERENCE_CONCURRENCY` 실측값 | 2 | M1 benchmark |
| E | `no_face`가 2인 방에서 2위가 되는 것의 UX | 포인트 30 고정 유지 (D-2) | M4 베타 |
| F | Redis 이미지 캐시의 메모리 상한 | 12명 × 100방 × ~120KB ≈ 145MB. 인스턴스 512MB 확보 | M5 부하 테스트 |
| G | 쿠키 시크릿 로테이션 운영 절차 | 2키 30일 창 (§6.1) | M5 |

PRD 13장의 열린 미결 중 백엔드에 영향이 있는 것은 다음과 같으며, 모두 **현재 구현을 유지한 채 베타에서 관찰**한다.

- 미결 3(포인트 배점 A/B) — §11.3을 전략 객체로 분리해 두면 교체 비용이 낮다. MVP는 순위 기반 단일 구현.
- 미결 6(권한 거부자 배제), 미결 9(먼저 낸 사람의 대기), 미결 11(미제출 페널티 강도) — 서버 규칙 변경 없이 지표만 계측한다.
- 미결 10(1인 1방 우회) — 남용이 관측되면 §16에 IP 기준 방 생성 리밋을 강화하는 것으로 대응한다. 현재도 IP 10회/시간이 걸려 있다.
