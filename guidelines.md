# Backend Development Guidelines

## 1. 개발 목표

Backend의 최우선 목표는 모든 참가자가 동일한 게임 상태를 바라보도록 하고, 게임 규칙을 서버에서 일관되게 적용하며, 업로드된 셀카를 안전하게 감정 판별한 뒤 즉시 폐기하는 것이다.

Backend는 다음 영역의 Single Source of Truth이다.

- 사용자 식별
- 방 상태
- 방장 권한
- 참여자 상태
- 게임 상태
- 라운드
- 라운드 deadline
- 제출 여부
- 감정 판별
- 점수
- 순위
- 좋아요
- 최종 결과

Client에서 전달한 값은 신뢰하지 않는다.

---

# 2. 기본 기술 스택

## Language

- Python 3.11

## API Framework

- FastAPI

## ASGI

- Uvicorn

## Realtime

- python-socketio

## Validation

- Pydantic v2

## Database

- PostgreSQL

## ORM

- SQLAlchemy 2.x
- asyncpg

## Migration

- Alembic

## Realtime / Temporary State

- Redis

## AI

- PyTorch
- torchvision
- MediaPipe Tasks API
- Pillow
- NumPy

## Deployment

- Docker
- Kubernetes

---

# 3. Backend Architecture

초기 Backend는 Modular Monolith 형태로 개발한다.

MVP 단계에서 Game Server와 AI Inference를 별도 Microservice로 분리하지 않는다.

권장 구조:

```text
app/
├── api/
├── realtime/
├── domain/
├── inference/
├── db/
└── core/
```

도메인은 가능한 다음 기준으로 분리한다.

```text
User
Room
Participant
Game
Round
Submission
Like
```

AI 관련 코드는 `inference` 영역으로 명확히 분리한다.

---

# 4. femo_web.py 관련 원칙

`femo_web.py`는 서비스 Backend 코드가 아니다.

해당 파일은 사용할 감정 인식 모델과 처리 방식을 검증하기 위해 작성된 PoC이다.

Production Backend에서 다음 요소를 직접 사용하지 않는다.

```text
❌ Gradio UI
❌ femo_web.predict()
❌ matplotlib chart
❌ Gradio monkeypatch
❌ Gradio lazy singleton 구현
❌ URL 입력 기능
```

PoC에서 활용하는 것은 검증된 기술적 사실이다.

예:

- 감정 모델 종류
- 7개 emotion label
- 얼굴 crop 필요성
- MediaPipe FaceDetector 사용 가능 여부
- 모델 preprocessing 방식

Production inference 코드는 별도로 작성한다.

---

# 5. AI Inference 경계

게임 로직이 PyTorch 구현 세부사항에 직접 의존하지 않도록 한다.

다음과 같은 추상화를 둔다.

```python
class EmotionClassifier(Protocol):
    async def classify(
        self,
        image: bytes,
    ) -> EmotionResult: ...
```

게임 영역에서는 다음만 알아야 한다.

```text
이미지
 ↓
EmotionClassifier
 ↓
EmotionResult
```

게임 코드에서 직접 다음을 호출하지 않는다.

```text
torch.load()
MediaPipe
softmax()
model(...)
```

---

# 6. Production Inference Pipeline

서비스의 기본 inference pipeline은 다음과 같다.

```text
Uploaded JPEG
      ↓
Image Decode
      ↓
Face Detection
      ↓
Largest Face Selection
      ↓
Face Crop
      ↓
224 × 224
      ↓
RGB → BGR
      ↓
VGGFace2 mean subtraction
      ↓
ResNet50
      ↓
Softmax
      ↓
7 Emotion Probabilities
```

기존 검증 코드와 동일한 preprocessing 규칙을 유지한다.

임의로 다음 처리를 추가하지 않는다.

```text
❌ /255 normalization
❌ ImageNet mean/std normalization
```

모델을 변경할 경우 preprocessing 역시 모델 버전의 일부로 관리한다.

---

# 7. 얼굴 검출

얼굴이 검출되지 않았을 경우 원본 이미지를 감정 모델에 넣지 않는다.

Production에서는 다음과 같이 처리한다.

```text
Face not detected
      ↓
NO_FACE
      ↓
라운드 점수 0
```

다중 얼굴이 검출될 경우 첫 번째 detection을 무조건 사용하지 않는다.

bounding box의 면적을 기준으로 가장 큰 얼굴을 선택한다.

```text
width × height
```

가 가장 큰 얼굴을 사용한다.

---

# 8. Image Upload

이미지는 HTTP multipart upload를 사용한다.

Socket.IO로 이미지 binary를 전달하지 않는다.

예:

```http
POST /rooms/{roomId}/rounds/{roundId}/submissions
```

Socket.IO는 다음과 같은 상태 변경을 전달한다.

```text
submission:status
submission:scored
round:finalized
round:closed
```

---

# 9. Image Storage

사용자가 촬영한 이미지는 영구 저장하지 않는다.

다음 저장 방식을 사용하지 않는다.

```text
❌ PostgreSQL BLOB
❌ Object Storage
❌ Persistent Volume
❌ 영구 filesystem
```

처리 흐름은 다음과 같아야 한다.

```text
HTTP Body
 ↓
Memory
 ↓
Image Decode
 ↓
Face Detection
 ↓
Inference
 ↓
Result
 ↓
Memory release
```

임시 파일 사용도 필요하지 않다면 피한다.

로그에 다음 값을 남기지 않는다.

- 이미지 원본
- base64 이미지
- 얼굴 crop
- 식별 가능한 이미지 파생 데이터

---

# 10. Client 입력을 신뢰하지 않는다

다음 값은 Client가 보내더라도 판정 근거로 사용하지 않는다.

```text
clientSubmittedAt
clientScore
clientRank
clientPoints
clientHost
```

특히 제출 마감 판정은 서버가 요청을 받기 시작한 시각을 기준으로 한다.

```text
HTTP request 도착
      ↓
serverReceivedAt 기록
      ↓
round.deadlineAt 비교
      ↓
업로드 body 처리
```

body upload가 완료된 시간을 제출 시각으로 사용하지 않는다.

---

# 11. Round Deadline

라운드 deadline은 Backend가 생성한다.

예:

```text
round.startedAt
round.deadlineAt
```

Client에게는 절대 시각을 전달한다.

Client는 해당 값을 표시만 한다.

Backend에서는 deadline 이후 시작된 제출 요청을 거부한다.

---

# 12. Realtime

실시간 이벤트는 python-socketio를 사용한다.

모든 이벤트는 다음 원칙을 따른다.

### Server Authoritative

게임 상태 변경은 서버가 결정한다.

### Room Scoped

가능하면 방 단위로 broadcast한다.

### Authorization

모든 이벤트에 대해 현재 socket/user/participant가 해당 정보를 받을 권한이 있는지 판단한다.

---

# 13. 미제출자의 결과 접근 제한

미제출자의 결과를 Client에서 숨기는 방식으로 구현하지 않는다.

Backend가 해당 사용자에게 결과 이벤트를 발행하지 않아야 한다.

예를 들어 미제출 사용자에게는 다음 상세 이벤트를 보내지 않는다.

```text
submission:scored
round:finalized
like:updated
```

사용자가 결과 URL 또는 화면에 직접 접근해도 Backend에서 데이터가 전달되지 않아야 한다.

---

# 14. UUID 기반 사용자 식별

로그인 시스템을 구현하지 않는다.

서버에서 UUID를 발급하고 Cookie를 이용해 사용자를 식별한다.

Cookie는 최소 다음 속성을 갖는다.

```text
HttpOnly
Secure
SameSite=Lax
```

Frontend JavaScript가 UUID 자체를 읽을 수 있다는 전제로 코드를 작성하지 않는다.

방장 권한은 Cookie 존재 여부만 보고 판단하지 않는다.

Backend의 User/Room 관계를 조회하여 검증한다.

---

# 15. Reconnect

재접속을 예외 상황이 아닌 정상적인 기능으로 취급한다.

Backend는 UUID를 이용해 기존 Participant를 찾고 현재 상태를 복원할 수 있어야 한다.

복원 대상:

- Room
- Participant
- Host 여부
- Game 상태
- Round 상태
- deadline
- Submission 상태
- 누적 점수

일시적인 socket disconnect만으로 Participant 데이터나 Room 데이터를 바로 삭제하지 않는다.

---

# 16. PostgreSQL 역할

PostgreSQL은 복원과 게임 규칙에 필요한 구조화 데이터를 관리한다.

주요 Entity:

```text
User
Room
Participant
Round
Submission
Like
```

DB Constraint를 적극적으로 사용한다.

예:

```text
활성 Room 기준 User당 소유 Room 1개

(roundId, voterParticipantId)
UNIQUE
```

애플리케이션 코드만으로 데이터 무결성을 보장하려고 하지 않는다.

---

# 17. Redis 역할

Redis는 영속 데이터베이스를 대체하지 않는다.

다음과 같은 실시간/휘발성 상태에 사용한다.

```text
Socket.IO multi-pod Pub/Sub
presence
connection mapping
round temporary state
deadline scheduling
host disconnect timeout
rate limit
distributed coordination
```

PostgreSQL과 Redis의 역할을 명확하게 분리한다.

```text
PostgreSQL
= 복원 및 데이터 무결성

Redis
= 실시간 coordination
```

---

# 18. AI Model Loading

모델은 요청마다 로드하지 않는다.

Pod가 시작할 때 모델을 한 번 로드한다.

흐름:

```text
Pod Start
 ↓
Model Load
 ↓
FaceDetector Load
 ↓
Warmup
 ↓
Ready
```

첫 사용자의 요청에서 모델을 다운로드하거나 로드하는 구조를 사용하지 않는다.

---

# 19. Model Artifact 관리

모델 weight와 Backend Docker image lifecycle을 분리한다.

Docker image에는 다음을 포함한다.

```text
Backend source
Python dependencies
PyTorch
MediaPipe
Model architecture code
```

다음 모델 artifact는 Kubernetes Volume에서 공급한다.

```text
*.pt
*.tflite
```

예:

```text
/models/
├── emotion/
│   └── FER_static_ResNet50_AffectNet.pt
└── face/
    └── blaze_face_short_range.tflite
```

Application은 모델을 다운로드하는 책임을 갖지 않는다.

모델 파일이 없다면 서버 startup을 실패시킨다.

---

# 20. Kubernetes Model Volume

모델은 Persistent Volume을 기본 전제로 한다.

Backend Pod에서는 가능하면 read-only로 mount한다.

```text
Model Artifact
      ↓
Persistent Volume
      ↓
Backend Pod
```

Pod가 재시작되더라도 동일한 모델 artifact를 다시 다운로드할 필요가 없도록 한다.

모델 준비 또는 변경은 Application runtime이 아니라 Deployment 단계의 책임으로 취급한다.

---

# 21. Model Version

모델 버전은 명시적으로 관리한다.

예:

```text
EMOTION_MODEL_VERSION=v1

EMOTION_MODEL_PATH=/models/emotion/v1/model.pt
```

Backend version과 Model version을 동일하게 묶을 필요는 없다.

예:

```text
Backend
v1.4.2

Emotion Model
v2
```

모델 교체 때문에 Backend image 전체를 다시 설계하지 않도록 한다.

---

# 22. Kubernetes Pod

Backend Pod는 기본적으로 다음을 포함한다.

```text
FastAPI
Uvicorn
Socket.IO
PyTorch
MediaPipe
Loaded Emotion Model
```

여러 Uvicorn Worker를 하나의 Pod 안에서 무작정 증가시키지 않는다.

Worker마다 PyTorch Model이 별도로 메모리에 올라갈 수 있기 때문이다.

초기 권장 단위:

```text
1 Pod
=
1 Uvicorn process
=
1 loaded model
```

성능이 필요하면 Worker 수보다 Pod replica를 먼저 조정한다.

---

# 23. Kubernetes Scale-out

다수 Pod 환경을 기본적으로 고려한다.

```text
Ingress
   │
 ┌─┼─────────┐
 │ │         │
Pod A       Pod B
 │           │
 └────Redis──┘
```

Room 상태나 Socket broadcast가 특정 Pod의 process memory에만 존재해서는 안 된다.

필요한 공유 상태와 event propagation에 Redis를 사용한다.

---

# 24. Kubernetes Health Check

다음 endpoint를 둔다.

```text
/health/live
/health/ready
```

`live`는 프로세스가 정상 실행 중인지 확인한다.

`ready`는 최소 다음 조건 이후에만 성공한다.

```text
Application initialized
Model loaded
FaceDetector loaded
Warmup completed
필수 dependency 연결 가능
```

모델이 준비되지 않은 Pod가 사용자 요청을 받지 않도록 한다.

---

# 25. AI Concurrency

PyTorch 및 MediaPipe 연산을 FastAPI event loop에서 무제한 병렬 실행하지 않는다.

MediaPipe detector와 Model의 thread safety 및 CPU/GPU 자원 사용량을 고려한다.

Inference concurrency를 명시적으로 제한한다.

예:

```text
Inference Request
      ↓
Semaphore
      ↓
FaceDetector
      ↓
PyTorch
```

정확한 동시 실행 수는 benchmark를 통해 결정한다.

임의로 높은 concurrency 값을 적용하지 않는다.

---

# 26. Security

Backend는 최소 다음 검증을 수행한다.

### Upload

- Content-Type
- 최대 request size
- 실제 image decode 가능 여부
- 지원하지 않는 이미지 데이터 거부

### Room

- 유효한 Room인지 검증
- 유효한 Round인지 검증
- 현재 Participant인지 검증

### Submission

- 현재 Round인지 검증
- deadline 검증
- 중복 제출 검증

### Host Action

- Server-side host validation

### Rate Limit

- Room 생성
- 비정상 입장 시도
- 과도한 upload 요청

---

# 27. 테스트 목표

다음 로직은 반드시 자동 테스트 대상에 포함한다.

```text
UUID 사용자 복원

1인 1 활성 방

Host reconnect

Host 60초 이탈

Round deadline

마감 직전 upload

마감 이후 upload

중복 제출

Face not detected

Multiple faces

Inference failure

Inference timeout

동점 순위

Like 1인 1표

Self like 금지

Missed participant result 차단

Reconnect state restore
```

게임 규칙은 UI 테스트보다 Backend unit/integration test에서 우선 검증한다.

---

# 28. Backend가 하면 안 되는 것

```text
❌ femo_web.py를 Production API로 사용

❌ Gradio 의존성을 서비스 Backend에 추가

❌ Client가 보낸 점수를 신뢰

❌ Client timestamp로 deadline 판정

❌ 사용자 이미지를 영구 저장

❌ 모델을 request마다 로드

❌ Application runtime에서 임의로 모델 다운로드

❌ 특정 Pod memory만으로 Room 상태 관리

❌ 얼굴 미검출 시 원본 이미지로 감정 판별
```

---

# 29. 성능 목표

현재 Backend는 최소 다음 제품 목표를 고려한다.

```text
동시 진행 Room: 100

최대 동시 Participant: 1,200

Room당 최대 Participant: 12

Upload 완료 → 결과 공개:
p95 3초 이내

Client 상태 전환 편차:
500ms 이내

10초 이내 reconnect:
현재 게임 복원
```

최적화는 실제 benchmark 결과를 기준으로 한다.

초기부터 불필요한 분산 시스템이나 Microservice를 도입하지 않는다.

---

# 30. Definition of Done

Backend 기능은 다음 조건을 만족해야 완료로 본다.

### Requirement

요구사항 ID와 구현이 연결되어 있다.

예:

```text
RO-10
RD-08
CP-08
SC-01
RS-12
```

### Server Authority

Client가 임의 값을 보내도 게임 결과를 조작할 수 없다.

### Realtime

다수 참가자가 동일한 Room/Round 상태를 받는다.

### Reconnect

UUID 기반으로 현재 상태를 복원할 수 있다.

### Image

사용자 사진이 영구 저장되지 않는다.

### AI

모델이 startup에서 로드된다.

얼굴 없음과 inference 실패가 구분된다.

### Kubernetes

Pod restart 이후 정상 복구된다.

모델 volume이 정상 mount된다.

readiness 이전에는 traffic을 받지 않는다.

다수 Pod 환경에서도 realtime 상태가 정상 동작한다.

---

# 31. 최우선 목표

Backend 개발에서 가장 중요한 순서는 다음과 같다.

1. 서버가 게임 상태의 유일한 기준이 되는가
2. 라운드와 제출 deadline이 정확한가
3. 재접속 시 사용자와 게임 상태가 복원되는가
4. 실시간 이벤트가 올바른 사용자에게만 전달되는가
5. 이미지가 영구 저장되지 않는가
6. AI inference 결과가 게임 규칙과 정확히 연결되는가
7. AI inference가 다른 API와 realtime 처리를 막지 않는가
8. Kubernetes에서 Pod가 여러 개로 늘어나도 동일하게 동작하는가

복잡한 분산 아키텍처보다 게임 규칙의 정확성, 실시간 상태 일관성, inference 안정성을 우선한다.