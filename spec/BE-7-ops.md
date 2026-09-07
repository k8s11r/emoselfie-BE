# BE-7 · ops

**의존** 횡단 — 모든 모듈이 사용. 골격은 BE-1보다 **먼저** 세운다
**요구사항** 7.1 성능, 7.2 실시간, 7.4 보안 · 가이드 §12, §17, §22 ~ §26, §29

---

## 목표

Pod가 여러 개여도, Pod가 죽었다 살아나도 게임이 이어지게 한다. 그리고 방을 무한히 만드는 사람을 막는다.

이 모듈을 나중에 붙이면 안 된다. 단일 Pod 전제로 짠 Room 상태 관리는 멀티 Pod 대응 시 전면 재작업이 된다.

---

## PostgreSQL과 Redis의 역할

```
PostgreSQL  =  복원 및 데이터 무결성
Redis       =  실시간 coordination
```

역할을 섞지 않는다. Redis는 영속 데이터베이스를 대체하지 않는다 (가이드 §17).

### PostgreSQL

Entity: `User` `Room` `Participant` `Round` `Submission` `Reaction` `RoundSkip`

DB 제약을 적극적으로 쓴다. 애플리케이션 코드만으로 무결성을 보장하려 하지 않는다 (§16).

```sql
-- 활성 Room 기준 User당 1개                          RO-02
CREATE UNIQUE INDEX uq_active_room_per_host ON rooms (host_user_id)
  WHERE status IN ('waiting','playing');

UNIQUE (room_id, user_id)                          -- Participant 중복
UNIQUE (room_id, index)                            -- Round 번호
UNIQUE (room_id, target_emotion)                   -- RD-02 감정 중복 금지
UNIQUE (round_id, participant_id)                  -- 중복 제출
UNIQUE (target_submission_id, actor_participant_id, type)  -- RX-02·03
UNIQUE (round_id, participant_id)                  -- RoundSkip
```

### Redis

```
Socket.IO multi-pod Pub/Sub     ← AsyncRedisManager
presence / connection mapping   conn:{userId} → sid          (ID-07)
round temporary state
deadline scheduling             (RD-08)
host disconnect timeout         host_gone:{roomId} TTL 60s   (RO-10)
room expiry scheduling          (RO-14)
submission image cache          TTL = 라운드 종료 + 여유      (PV-02)
rate limit
distributed coordination        (분산 락)
```

**스케줄된 작업을 특정 Pod의 `asyncio.Task`로만 유지하지 않는다.** 그 Pod가 죽으면 라운드가 영영 안 끝나고 방장 위임이 일어나지 않는다. Redis 키 만료 이벤트 또는 주기 스캐너 + 분산 락으로 구현한다. **[가정]** — 원본 문서는 "Redis로 한다"까지만 정했다.

---

## 멀티 Pod

```
        Ingress
           │
     ┌─────┼─────┐
     │           │
   Pod A       Pod B
     │           │
     └──Redis────┘
```

- Room 상태나 Socket broadcast가 특정 Pod의 process memory에만 존재해서는 안 된다 (§23).
- Socket.IO는 `AsyncRedisManager`를 붙인다. Pod A에서 발행한 이벤트가 Pod B에 붙은 참여자에게 가야 한다.
- **sticky session에 의존하지 않는다.** 재접속 시 다른 Pod에 붙어도 정상 복원되어야 한다.
- 라운드 마감·방 소멸·방장 위임처럼 **한 번만 실행되어야 하는 작업**은 분산 락으로 감싼다.

### Pod 구성

```
1 Pod  =  1 Uvicorn process  =  1 loaded model
```

Worker를 늘리면 Worker마다 PyTorch 모델이 별도로 메모리에 올라간다. 성능이 필요하면 **Worker 수보다 replica를 먼저 조정한다** (§22).

### Health Check

```
GET /health/live    프로세스 정상 실행 중
GET /health/ready   아래 전부 만족한 뒤에만 성공
```

`ready` 조건 (§24):

```
Application initialized
Model loaded
FaceDetector loaded
Warmup completed
필수 dependency 연결 가능 (PostgreSQL, Redis)
```

모델이 준비되지 않은 Pod가 사용자 요청을 받아서는 안 된다. `live`가 dependency 상태를 검사하면 DB가 잠깐 흔들릴 때 Pod가 무한 재시작한다 — `live`는 프로세스만 본다.

---

## 보안 (7.4, §26)

| 대상 | 검증 |
|---|---|
| Upload | Content-Type · 최대 크기 · 실제 디코드 가능 여부 · 미지원 형식 거부 |
| Room | 유효 Room · 유효 Round · 현재 Participant |
| Submission | 현재 Round · deadline · 중복 제출 · 촬영 세션 토큰 |
| Host Action | **서버 측 host validation** (쿠키 존재만으로 판정 금지) |
| 전송 | 전 구간 HTTPS (PM-14) |
| 채점 | 모든 채점은 서버에서. Client가 자기 점수·순위를 조작할 수 없다 |

### Rate Limit

| 대상 | 근거 | 제안값 **[가정]** |
|---|---|---|
| 방 생성 (IP 기준) | 7.4 "동일 IP의 대량 방 생성" | 10회 / 시간 |
| 방 입장 시도 (IP 기준) | 7.4 "무차별 입장 시도" | 30회 / 분 |
| 업로드 (참여자 기준) | §26 "과도한 upload 요청" | 라운드당 3회 |
| 리액션·스킵 (참여자 기준) | 토글 남용 | 10회 / 10초 |

정확한 값은 베타 트래픽을 보고 조정한다. Redis 기반이므로 Pod 간에 합산된다.

방 slug가 12자 URL-safe 난수인 것이 무차별 입장의 1차 방어이고, 레이트 리밋은 2차다 (C-8).

---

## 성능 목표 (7.1, §29)

```
동시 진행 Room             100
최대 동시 Participant      1,200
Room당 최대 Participant    12
초대 링크 → 대기실 렌더링   p95 2초 이내 (4G)
Upload 완료 → 결과 공개    p95 3초 이내
Client 상태 전환 편차       500ms 이내
10초 이내 reconnect        현재 게임 복원
```

**최적화는 실제 benchmark 결과를 기준으로 한다.** 초기부터 불필요한 분산 시스템이나 Microservice를 도입하지 않는다 (§29). MVP는 Modular Monolith다 — Game Server와 AI Inference를 분리하지 않는다 (§3).

측정 지점:

- `submission received → submission:scored 발행`까지의 p95 (3초 목표)
- inference 세마포어 대기 시간 (event loop 블로킹의 조기 경보)
- Socket.IO 이벤트 발행 → 수신 지연 (500ms 목표)
- Redis / PostgreSQL 커넥션 풀 포화

---

## 관측

구조화 로그(JSON) + 요청별 correlation id. **[가정]**

로그에 절대 넣지 않는 것 (PV-05):

```
이미지 원본   base64 이미지   얼굴 crop   식별 가능한 이미지 파생 데이터
사용자 UUID (participantId 로 대체)
```

PRD 2.2의 지표를 계측할 수 있게 이벤트를 남긴다 (M4에서 필요).

```
방 생성 → 게임 시작 전환율     세션 완주율
방장 권한 복원율               라운드 제출률
평균 판별 지연 (p95 3초)
```

---

## 테스트

- 2 Pod 환경에서 Pod A의 제출이 Pod B 참여자에게 전달됨
- Pod A가 등록한 라운드 마감을 Pod A 종료 후 Pod B가 처리
- Pod A가 등록한 방장 60초 타이머를 Pod A 종료 후 Pod B가 처리
- 분산 락으로 마감 처리가 정확히 1회만 실행됨
- 재접속 시 다른 Pod에 붙어도 상태 복원 (sticky 미의존)
- 모델 미로드 상태에서 `/health/ready` 실패, `/health/live` 성공
- DB 연결 끊김 시 `ready` 실패, `live`는 유지 → Pod 재시작 안 됨
- 모델 파일 없음 → startup 실패
- IP당 방 생성 레이트 리밋 동작, Pod 간 합산
- 로그 전체에서 base64·이미지 바이트·UUID 문자열 미검출

---

## 하지 않는 것

```
❌ Room 상태를 process memory에만 보관
❌ 스케줄 작업을 단일 Pod asyncio.Task로만 유지
❌ sticky session 전제
❌ Pod 안에서 Uvicorn worker 수를 무작정 증가
❌ /health/live 에서 dependency 검사 (무한 재시작)
❌ Redis 를 영속 저장소로 사용
❌ benchmark 없이 concurrency·풀 크기 임의 설정
❌ MVP 단계에서 Microservice 분리
❌ 로그에 UUID·이미지 파생물 기록
```
