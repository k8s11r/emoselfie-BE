# BE-5 · submission

**의존** BE-3 game-loop, BE-4 inference
**요구사항** CP-03, CP-05, CP-08 ~ CP-10, SC-01 ~ SC-09, PV-01, PV-02
**PRD** 5.6 촬영 및 업로드 · 5.7 판별 및 채점 · 가이드 §8 ~ §11, §26

---

## 목표

업로드를 받고, **마감 안에 들어왔는지 서버 기준으로 판정하고**, 판별을 돌려 점수와 순위를 낸다. 클라이언트가 보낸 어떤 값도 판정에 쓰지 않는다.

---

## 제출 판정 — 이 모듈의 핵심

| ID | 규칙 |
|---|---|
| CP-08 | **제출 여부는 서버가 업로드 요청을 수신하기 시작한 시각으로 판정한다.** 그 시각이 `deadlineAt` 이전이면 정상 제출이며, 이미지 전송이 마감 이후까지 이어져도 무관하다. |
| CP-09 | 클라이언트가 보낸 촬영·제출 시각은 위조 가능하므로 판정 근거로 쓰지 않는다. |
| CP-10 | 마감 이후 도착한 요청은 거부하고 미제출 처리한다. **재시도를 유도하지 않는다.** |

```
HTTP request 도착
      ↓
serverReceivedAt 기록      ← 미들웨어에서 body 읽기 전에
      ↓
round.deadlineAt 비교
      ↓ (통과)
업로드 body 처리
```

`serverReceivedAt`은 **body를 읽기 전에** 미들웨어에서 기록한다. body 수신 완료 시각을 쓰면 4G에서 파일이 큰 사용자가 억울하게 탈락한다. 이게 CP-08 문장의 전부다.

무시하는 필드:

```
clientSubmittedAt   clientScore   clientRank   clientPoints   clientHost
```

Pydantic 스키마에 아예 넣지 않는다. 받아서 버리는 것보다 받지 않는 것이 안전하다.

---

## 업로드

이미지는 **HTTP multipart**로 받는다. Socket.IO로 binary를 보내지 않는다 (가이드 §8).

```
POST /api/rooms/{slug}/rounds/{roundId}/submissions
Content-Type: multipart/form-data
```

### 검증 (가이드 §26)

| 항목 | 검증 |
|---|---|
| Content-Type | `multipart/form-data`, 파트는 `image/jpeg` |
| 최대 크기 | `MAX_UPLOAD_BYTES` 초과 시 즉시 거부. **[가정]** 512KB — FE가 720px/0.8로 압축하므로 여유값 |
| 디코드 | 실제로 이미지로 디코드되는지 확인. 확장자·헤더만 믿지 않는다 |
| 형식 | 지원하지 않는 이미지 데이터 거부 |
| Room | 유효한 방인지 |
| Round | **현재** 라운드인지 (지난 라운드 ID로 밀어넣기 차단) |
| Participant | 현재 이 방의 참여자인지 |
| Deadline | CP-08 |
| 중복 | 이미 제출했는지 |
| 세션 토큰 | CP-03 |

| ID | 규칙 |
|---|---|
| CP-03 | 제출 이미지는 인앱 프리뷰 캡처만 허용한다. **정상 촬영 세션에서 발급된 토큰이 없는 업로드를 서버가 거부한다.** |

촬영 세션 토큰: `round:revealed` 시 참여자별로 단기 토큰을 발급하고, 업로드 시 헤더로 요구한다. 라운드·참여자에 바인딩되고 1회용이며 `deadlineAt`에 만료된다. **[가정]** — PRD는 "토큰이 없는 업로드를 거부한다"만 규정하고 발급 방식은 정하지 않았다.

이 토큰은 사진첩 업로드를 완벽히 막지 못한다. 결정된 브라우저 조작 앞에서는 뚫린다. 목적은 우연한·손쉬운 우회를 막는 것이다.

중복 제출은 DB 유니크 제약으로 막는다: `UNIQUE(roundId, participantId)`.

---

## 채점

| ID | 규칙 |
|---|---|
| SC-01 | 라운드 점수 = **제시된 감정 라벨의 신뢰도 × 100**, 소수점 첫째 자리 (예: 87.3점). |
| SC-02 | 라운드 순위는 점수 내림차순. 순위 포인트 **1위 100 / 2위 70 / 3위 50 / 그 외 30 / 미제출 0**. |
| SC-03 | 동점 시 **먼저 제출한 사용자**를 상위로 한다. |
| SC-04 | 얼굴 미검출 → 그 라운드 0점, "얼굴을 찾지 못했어요" 표시. |
| SC-05 | 엔진 처리 실패(타임아웃·오류) → **판정 불가**. 0점이 아니며 총점 계산 시 페널티 없음. |
| SC-06 | **라운드 획득 포인트 = 순위 포인트.** 리액션은 점수에 반영하지 않는다. 최종 순위는 누적 포인트로 결정하고, 동점 시 최고 단일 라운드 점수가 높은 쪽이 상위. |
| SC-07 | 판별 응답 5초 초과 시 타임아웃 (BE-4). |
| SC-09 | **순위 포인트는 최종 정렬이 확정된 시점에 계산한다.** 결과 화면의 중간 순위는 표시용이며 포인트를 확정하지 않는다. |
| SC-08 | "가장 엉뚱한 결과" 뱃지 (P2). |

SC-09가 중요하다. 제출할 때마다 순위가 바뀌므로, 중간 순위로 포인트를 확정하면 먼저 낸 사람이 부당하게 이득을 본다. `submission:scored`가 나르는 `currentRank`는 **표시용**이고, `rankPoints`는 `round:finalized`에서만 채워진다.

SC-03의 "먼저 제출한"의 기준은 `serverReceivedAt`이다. 클라이언트 시각이 아니다.

SC-05 판정 불가는 순위 산정에서 제외한다. 0점으로 넣으면 꼴찌가 되어 사실상 페널티가 된다.

### 상태

```
submitted   정상 채점됨
no_face     얼굴 미검출 → 0점            (SC-04)
failed      엔진 실패 → 판정 불가         (SC-05)
missed      마감 내 미제출 → 0점          (RD-06)
```

`no_face`와 `failed`는 사진이 존재하므로 리액션 대상이다 (RX-08). 결과도 정상적으로 볼 수 있다 (RS-13). 페널티 대상은 `missed`뿐이다.

### 실패 처리

| 상황 | 처리 |
|---|---|
| CP-05 업로드 실패 | FE가 자동 1회 재시도. 서버는 멱등하게 처리 (같은 라운드 중복 제출 거부) |
| 엔진 전체 장애 | 라운드 무효 처리 + 재시도 안내. **3회 연속 실패 시 게임 중단 + 현재 순위 공개** |

---

## 이미지 수명

| ID | 규칙 |
|---|---|
| PV-01 | **영구 저장소에 기록하지 않는다.** 판별에 쓰인 원본 바이트는 처리 직후 메모리에서 해제한다. |
| PV-02 | 결과 화면 사진은 **서버 임시 캐시에 보관하고 라운드 종료 시점에 파기**한다. TTL은 만료 안전망으로 함께 건다. 세션 종료 후 조회 경로가 존재해서는 안 되고, 조회는 결과 열람 권한이 있는 참여자에게만 허용한다. |

```
HTTP Body → Memory → Decode → Inference → Result → Memory release
```

금지: PostgreSQL BLOB, Object Storage, Persistent Volume, 영구 filesystem.

### 결과 화면 사진 전달 — Redis 임시 캐시 (확정)

캐러셀은 남의 사진을 보여줘야 한다. **압축 JPEG를 Redis에 잠깐 두고, 이벤트에는 URL만 싣는다.**

```
제출 → 판별 → Redis SET  img:{submissionId}  (TTL)
            → submission:scored { imageUrl: "/api/rounds/{rid}/submissions/{sid}/image" }
```

**이벤트에 이미지를 실어 보내는 방식은 채택하지 않았다.** 5초에 제출한 사람의 사진은 6초에 발행되는데, 8초에 결과 화면으로 들어온 사람은 그 이벤트를 못 받고 다시 받을 방법도 없다. 진입 시 밀린 사진을 몰아주려면 12명 방 기준 base64로 1.6MB를 한 번에 보내야 하고, 4G에서 화면이 멈춘다. Socket.IO로 이미지를 나르지 말라는 가이드 §8과도 어긋난다.

| 항목 | 값 |
|---|---|
| 저장소 | Redis (멀티 Pod 공유. 특정 Pod 메모리에 두지 않는다) |
| 키 | `img:{submissionId}` |
| 값 | 업로드된 720px JPEG 원본 바이트 |
| TTL | `round.endedAt + 감상시간 + 30초` — **안전망일 뿐** |
| 실제 파기 | 라운드 종료 시 **명시적 DEL** |
| 용량 | 12명 × 100KB × 100방 ≈ 120MB |
| 서빙 | 참여자 인증 + RS-12 차단 통과 시에만 |

TTL만 믿고 명시적 삭제를 생략하지 않는다. TTL은 프로세스가 죽어 DEL을 못 돌린 경우의 백스톱이다.

**대외 문구를 실제에 맞춰 고쳤다.** PV-01은 "영구 저장 금지"로 범위가 명확해졌고, PV-03의 사용자 고지는 "판별 후 즉시 삭제" → **"사진은 라운드가 끝나면 바로 삭제돼요"** 로 바뀌었다 (PRD v1.1). 60초간 서버에 있는 것을 "즉시 삭제"라고 안내하지 않는다.

---

## 데이터

```
Submission
  id             UUID PK
  roundId        UUID FK→Round
  participantId  UUID FK→Participant
  submittedAt    timestamptz      -- serverReceivedAt (CP-08). 판정과 SC-03의 기준
  status         enum(submitted|no_face|failed|missed)
  targetScore    numeric(4,1)     -- SC-01, 소수점 1자리
  topEmotions    jsonb            -- 상위 3개 (RS-10, P1)
  rank           int NULL         -- 최종 정렬 시 확정
  rankPoints     int NULL         -- SC-09. finalized 전에는 NULL
  pointsAwarded  int NULL         -- = rankPoints (SC-06)
  likeCount      int DEFAULT 0
  questionCount  int DEFAULT 0
  modelVersion   text
  -- UNIQUE(roundId, participantId)
  -- 이미지 바이너리 컬럼 없음
```

`pointsAwarded`를 `rankPoints`와 따로 두는 이유는 v1.0에서 보너스가 사라졌지만 PRD 데이터 모델이 두 필드를 유지하기 때문이다. 항상 같은 값이다.

---

## 인터페이스

```
POST /api/rooms/{slug}/rounds/{roundId}/submissions
  Headers: X-Capture-Token
  Body:    multipart/form-data (image/jpeg)
  → 202 {submissionId, status: "accepted"}      채점은 비동기, 결과는 소켓으로
  → 409 {code: "ALREADY_SUBMITTED"}
  → 410 {code: "DEADLINE_PASSED"}               재시도 유도하지 않음 (CP-10)
  → 413 {code: "TOO_LARGE"}
  → 422 {code: "INVALID_IMAGE"}

GET /api/rounds/{roundId}/submissions/{submissionId}/image
  참여자 인증 + RS-12 차단 통과 시에만
```

채점 완료는 BE-6이 `submission:scored`로 발행한다.

---

## 테스트

```
[마감]
  마감 1ms 전 수신 시작 → 인정
  마감 1ms 후 수신 시작 → 거부, 미제출
  수신 시작은 마감 전 · body 완료는 마감 후 → 인정 (CP-08 핵심)
  clientSubmittedAt 을 과거로 위조 → 무시, 서버 시각으로 판정 (CP-09)
[검증]
  촬영 세션 토큰 없음 → 거부 (CP-03)
  다른 참여자의 토큰 → 거부
  지난 라운드 ID로 제출 → 거부
  방 참여자 아님 → 거부
  중복 제출 → 409 (DB 제약으로도 막힘)
  512KB 초과 → 413
  JPEG가 아닌 바이트 → 422
[채점]
  확률 0.873 → 87.3점 (SC-01)
  순위 포인트 100/70/50/30/0 (SC-02)
  동점 → serverReceivedAt 이른 쪽 상위 (SC-03)
  no_face → 0점, 결과 열람 가능 (SC-04, RS-13)
  failed → 판정 불가, 순위 산정 제외, 총점 페널티 없음 (SC-05)
  finalized 전 rankPoints IS NULL (SC-09)
  최종 순위 동점 → 최고 단일 라운드 점수 비교 (SC-06)
[이미지]
  DB·파일시스템 어디에도 이미지 바이트가 없다
  라운드 종료 후 이미지 엔드포인트 404
  미제출자가 이미지 엔드포인트 호출 → 거부 (RS-12)
  로그에 base64·이미지 바이트 없음
```

---

## 하지 않는 것

```
❌ body 수신 완료 시각으로 마감 판정
❌ 클라이언트 timestamp·점수·순위 신뢰
❌ Socket.IO로 이미지 binary 수신
❌ 이미지 영구 저장 (BLOB / Object Storage / PV / filesystem)
❌ 마감 초과 시 재시도 유도 (CP-10)
❌ failed를 0점으로 처리 (SC-05)
❌ 중간 순위로 rankPoints 확정 (SC-09)
❌ 리액션을 점수에 가산 (SC-06)
❌ 촬영 세션 토큰 검증 생략
```
