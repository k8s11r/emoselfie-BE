# emoselfie-BE

이모셀피 백엔드입니다. M0 기반, 세션·방·실시간 대기실, 실제 감정 추론 엔진을 구현했습니다. 쿠키 인증, 방 생성·입장·설정·닫기, 대기실 복원과 두 서버 간 Socket.IO 갱신, 실제 모델의 JPEG→7감정 판별을 테스트할 수 있습니다. 라운드 진행과 사진 업로드 API는 아직 구현하지 않았으므로 추론 엔진은 아직 HTTP로 노출되지 않습니다.

## 로컬 실행

Docker Compose와 `uv`가 필요합니다. Python 3.11은 `uv sync`가 준비하며 의존성은 `uv.lock`으로 고정합니다.

```sh
uv sync --locked
uv run python scripts/init_env.py
docker compose up -d --wait
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```

`scripts/init_env.py`는 `.env.example`에서 `.env`를 만들고 독립된 서명 키 3개를 생성합니다. 기존 `.env`는 덮어쓰지 않으며 생성 파일은 Git에서 제외합니다. 로컬 DB 계정은 개발 전용입니다.

기본적으로 브라우저 세션 연동은 FE와 BE를 같은 HTTPS 오리진에서 프록시합니다. `es_uid`는 기본적으로 `Secure; HttpOnly; SameSite=Lax`를 유지합니다. 로컬 HTTP 브라우저 개발이 필요하면 `APP_ENV=development`에서만 `ALLOW_INSECURE_COOKIE=true`를 명시합니다. 이 경우에도 HTTPS 요청에는 `Secure`를 유지하며, 운영·테스트 환경에서 해당 옵션을 켜면 시작을 거부합니다.

Traefik은 `/api`, `/media`, `/socket.io`, `/health`를 BE로 직접 전달할 수 있습니다. `SocketGateway`는 Engine.IO가 Origin을 검사하기 전에 `X-Forwarded-Proto`의 `ws/wss`를 `http/https`로 정규화합니다. Origin 검사는 계속 적용되며 Host의 포트를 보존해야 합니다. 쿠키 정책은 Uvicorn이 처리한 ASGI scheme을 사용합니다. 신뢰할 프록시 주소는 Uvicorn 설정으로 제한하고, TLS를 앞단에서 종료하면 Traefik의 `forwardedHeaders.trustedIPs`도 구성해야 합니다.

- [Liveness](http://127.0.0.1:8000/health/live): 프로세스 응답 확인
- [Readiness](http://127.0.0.1:8000/health/ready): DB·조율 Redis·이미지 Redis와 선택한 추론 엔진 확인
- [API 문서](http://127.0.0.1:8000/docs)

개발 설정은 `APP_ENV=development`, `INFERENCE_BACKEND=fake`입니다. readiness에 `inferenceBackend: "fake"`가 표시됩니다. 운영 환경은 fake를 거부하며, 모델 아티팩트가 없거나 크기·체크섬이 어긋나면 startup에 실패하고 fake로 대체하지 않습니다.

### 실제 모델 실행

추론 의존성(torch, mediapipe)과 아티팩트는 선택 설치입니다. 아티팩트는 `app/inference/artifacts.json`에 URL·sha256·크기로 고정되어 있고 준비 스크립트가 스트리밍 중 검증합니다.

```sh
uv sync --locked --extra inference
uv run python scripts/prepare_models.py --with-example
```

`.models/`에 내려받으며 Git에서 제외합니다. `--with-example`은 대조용 저자 데모 이미지까지 받습니다. 배포에서는 읽기 전용 볼륨의 `/models/...`를 사용하고, 로컬에서 실제 엔진을 쓰려면 `.env`의 `INFERENCE_BACKEND=real`과 함께 두 경로를 내려받은 위치로 바꿉니다.

```sh
EMOTION_MODEL_PATH=.models/FER_static_ResNet50_AffectNet.pt
FACE_MODEL_PATH=.models/blaze_face_short_range.tflite
```

전처리는 `/255`나 ImageNet 정규화를 쓰지 않습니다. 원본 0~255 BGR에서 모델의 VGGFace2 평균만 빼며, 이 값이 upstream 기준 구현과 정확히 일치하는지 모델 테스트가 확인합니다.

PostgreSQL은 `127.0.0.1:55432`, 조율 Redis는 `56379`, 이미지 Redis는 `56380`을 사용합니다. 두 Redis는 별도 인스턴스이며 이미지에는 RDB/AOF/볼륨을 사용하지 않습니다. 로컬 서비스를 중지하려면 `docker compose stop`을 사용합니다.

## 구현된 API

| 경로 | 동작 |
|---|---|
| `GET/PATCH /api/me` | 쿠키 발급·복원, 닉네임 저장, 활성 소유 방 조회 |
| `POST /api/rooms` | 신규 201, 기존 활성 방 200; UNIQUE 기반 경합 방어 |
| `GET /api/rooms/{slug}` | 입장 전 상태·정원·설정 요약 |
| `POST /api/rooms/{slug}/participants` | 멱등 입장, 12명 정원, 색상 배정. 서명 쿠키가 있는 세션만 |
| `PATCH /api/rooms/{slug}/settings` | 대기 중 방장만 변경 |
| `GET /api/rooms/{slug}/state` | 현재 참여자만 대기실 snapshot 조회 |
| `POST /api/rooms/{slug}/start` | 방장만, active 2명 이상일 때 게임 시작 |
| `POST /api/rooms/{slug}/rounds/{roundId}/submissions` | 촬영 토큰 검증·2MB 한도·중복 방어 후 202 |
| `POST /api/inference` | 시연용 JPEG 동기 추론. 방·라운드 없이 7개 감정 점수(0~100) 응답 |
| `GET /media/{token}` | 열람 권한자에게만 결과 사진, `private, no-store` |
| `POST /api/rooms/{slug}/close` | 대기 중 방장만 닫기, 소유 슬롯 반환 |
| `/socket.io` | 서명 쿠키·입장 인증, Redis 다중 서버 전파, 중복 연결 교체, presence |

대기실 변경은 현재 FE가 처리할 수 있는 `room:joined` 전체 snapshot으로 갱신합니다. `/start`는 감정 시퀀스를 뽑아 1라운드를 공개하고 `game:started`와 참여자별 `round:revealed`(개인 촬영 토큰)를 발행합니다.

라운드는 분산 스케줄러가 진행합니다. Pod마다 250ms 주기로 Redis ZSet을 폴링하고 ZREM 반환값으로 소유권을 정하므로 타이머는 여러 Pod에서도 한 번만 발화합니다. 마감 → 미제출 확정 → 채점 확정 → 감상 → 다음 라운드 → 최종 순위까지 서버가 스스로 넘어가며, 무효 라운드와 중단 종료도 처리합니다.

사진 업로드는 `POST /api/rooms/{slug}/rounds/{roundId}/submissions`입니다. 헤더 수신 시각으로 마감을 판정하고 촬영 토큰을 1회만 인정한 뒤 202를 반환하며, 추론은 백그라운드에서 돌아 `submission:scored`로 전달됩니다. 결과 사진은 이미지 Redis에 180초 TTL로 두고 열람자별 서명 토큰이 붙은 `GET /media/{token}`으로만 열립니다. 제출하지 않은 참여자에게는 어떤 결과 이벤트도 발행되지 않습니다. 늦게 제출해 결과 화면에 늦게 합류한 참여자에게는 이미 채점된 결과를 도착 순서대로 다시 보냅니다.

시연용 단일 사진 추론은 `POST /api/inference`에 `multipart/form-data`의
`image` 필드로 JPEG을 보내면 됩니다. 응답은 `faceDetected`, 최상위 `prediction`,
그리고 7개 감정의 0~100 `scores`를 포함합니다. 게임 제출 API와 달리 추론을
끝낼 때까지 HTTP 요청을 유지하며, IP당 기본 1분 10회로 제한됩니다.

각 서버는 60초 TTL의 생존 마커를 20초마다 갱신하고, 소켓은 그 마커가 살아 있을 때만 연결로 인정합니다. 서버가 비정상 종료하면 남은 소켓 키를 30초 주기 스윕이 죽은 것으로 판정해 정리합니다. 입장한 참여자가 60초 안에 소켓을 열지 않아도 슬롯을 반환합니다. 쿠키를 저장하지 못하는 브라우저는 소켓 인증을 통과할 수 없으므로 입장 자체를 `SESSION_REQUIRED`로 거절합니다. 두 장치가 없으면 한 사용자의 반복 입장이 방 정원을 채워 다른 사람이 `ROOM_FULL`을 받게 됩니다.

감상 단계에서는 `reaction:sent`로 like/question을 독립 토글하고 `round:skip`으로 다음 라운드를 앞당깁니다. 방장 스킵은 즉시, 그 외에는 연결된 열람자 전원이 눌러야 종료되며 리액션은 라운드가 닫힐 때 DB로 확정됩니다. 게임 중 snapshot·재접속은 여전히 503이며 임시 방장, 60초 이탈, 방 만료는 후속 구현입니다.

## 검증

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest

# 위 로컬 서비스와 migration이 준비된 상태
uv run alembic check
uv run pytest --run-integration

# 추론 extra와 준비된 아티팩트가 있는 상태
MODEL_DIR=.models uv run pytest --run-model
```

통합 테스트는 개발용 Redis를 그대로 쓰므로, 개발 서버를 띄워 둔 채로 실행하면 서버의 스케줄러가 테스트 타이머를 가져가 간헐적으로 실패합니다. 통합 테스트 전에는 개발 서버를 내려 주세요.

기본 pytest는 외부 서비스 없이 단위·계약 테스트를 실행하고 integration과 model은 명시적으로 건너뜁니다. `--run-model`은 실제 weight를 불러 저자 데모 이미지의 판정과 전처리 일치를 대조하므로 아티팩트가 없으면 실패합니다. 통합 테스트는 고유한 테스트 사용자·방·Redis 키만 생성하고 정리하며 전체 DB/Redis를 비우지 않습니다. DB·Redis는 고정된 로컬 개발 포트를 사용하고, 소켓 테스트는 임의의 빈 포트에 실제 Uvicorn 서버 2개를 실행한 뒤 정리합니다.

2026-09-08 기준 전체 204개 테스트(단위·계약 143, 통합 54, 모델 7), Ruff lint/format, mypy, migration 적용·롤백·재적용이 통과했습니다. 실제 FE 브라우저·모바일·게임 완주 검증과 원격 CI는 아직 실행하지 않았습니다.

로컬 benchmark(Apple M3, torch 1스레드, 동시성 2)에서 모델 로드는 1.41초, 단일 추론 p50은 24~30ms, 1440×1920 12장 동시 제출은 254ms였고 프로세스 RSS는 약 460MB였습니다. HTTP 업로드를 포함한 종단 측정은 아직 아닙니다. 자세한 수치는 [추적표](./docs/traceability.md)에 있습니다.

GitHub Actions는 같은 명령과 초기 migration의 `downgrade base → upgrade head`를 격리된 CI DB에서 검사합니다. `downgrade base`는 테이블을 삭제하므로 보존할 데이터가 있는 DB에서는 실행하지 않습니다.

## 현재 범위와 문서

- [개발 계획](./PLAN.md): 구조, 계약 게이트, M0~M5 구현 순서 및 완료 기준
- [TODO 리스트](./TODO.md): 우선순위·작업 크기·요구사항별 실행 체크리스트
- [백엔드 구현 명세](./spec.md)
- [Backend Development Guidelines](./guidelines.md)
- [구현 계약과 미확정 항목](./docs/contracts.md): 스키마 보강, G-01~12 상태와 후속 경계
- [요구사항 추적표](./docs/traceability.md): 구현 파일과 검증 근거

채점 규칙은 `domain/scoring`에 I/O 없는 순수 함수로 구현했고 명세 §18.1 표를 모두 덮었습니다. 다음 개발은 업로드 접수(BE-014·016)와 미디어 열람(BE-017), 이어서 라운드 상태 머신·분산 타이머입니다. 모델 아티팩트는 고정했지만 공급 경로 승인(G-01)과 업로드 재시도·접수(G-02·03), 게임 화면 복원 DTO(G-04), 채점 동점/평균(G-11)은 아직 확정되지 않았습니다.
