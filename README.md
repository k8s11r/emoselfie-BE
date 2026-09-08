# emoselfie-BE

이모셀피 백엔드입니다. M0 기반과 세션·방·실시간 대기실을 구현했습니다. 쿠키 인증, 방 생성·입장·설정·닫기, 대기실 복원과 두 서버 간 Socket.IO 갱신을 테스트할 수 있습니다. 라운드 진행·사진 업로드·실제 추론은 아직 구현하지 않았습니다.

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

위 HTTP 명령은 서버 개발·헬스체크용입니다. 브라우저 세션 연동은 FE와 BE를 같은 HTTPS 오리진에서 프록시해야 합니다. `es_uid` 쿠키는 개발에서도 `Secure; HttpOnly; SameSite=Lax`를 유지합니다. 신뢰할 프록시 주소는 Uvicorn 설정으로 제한하고 외부가 보낸 X-Forwarded-For를 직접 신뢰하지 않습니다.

- [Liveness](http://127.0.0.1:8000/health/live): 프로세스 응답 확인
- [Readiness](http://127.0.0.1:8000/health/ready): DB·조율 Redis·이미지 Redis와 선택한 추론 엔진 확인
- [API 문서](http://127.0.0.1:8000/docs)

개발 설정은 `APP_ENV=development`, `INFERENCE_BACKEND=fake`입니다. readiness에 `inferenceBackend: "fake"`가 표시됩니다. 운영 환경은 fake를 거부하고, 실제 모델 로더가 완성되기 전에는 startup에 실패합니다. 실제 모델 검증을 완료한 상태가 아닙니다.

PostgreSQL은 `127.0.0.1:55432`, 조율 Redis는 `56379`, 이미지 Redis는 `56380`을 사용합니다. 두 Redis는 별도 인스턴스이며 이미지에는 RDB/AOF/볼륨을 사용하지 않습니다. 로컬 서비스를 중지하려면 `docker compose stop`을 사용합니다.

## 구현된 API

| 경로 | 동작 |
|---|---|
| `GET/PATCH /api/me` | 쿠키 발급·복원, 닉네임 저장, 활성 소유 방 조회 |
| `POST /api/rooms` | 신규 201, 기존 활성 방 200; UNIQUE 기반 경합 방어 |
| `GET /api/rooms/{slug}` | 입장 전 상태·정원·설정 요약 |
| `POST /api/rooms/{slug}/participants` | 멱등 입장, 12명 정원, 색상 배정 |
| `PATCH /api/rooms/{slug}/settings` | 대기 중 방장만 변경 |
| `GET /api/rooms/{slug}/state` | 현재 참여자만 대기실 snapshot 조회 |
| `POST /api/rooms/{slug}/close` | 대기 중 방장만 닫기, 소유 슬롯 반환 |
| `/socket.io` | 서명 쿠키·입장 인증, Redis 다중 서버 전파, 중복 연결 교체, presence |

대기실 변경은 현재 FE가 처리할 수 있는 `room:joined` 전체 snapshot으로 갱신합니다. 게임 중 snapshot은 아직 제공하지 않으며 503을 반환합니다. `/start`는 라운드 실행기가 준비되기 전까지 노출하지 않습니다. 임시 방장·60초 이탈·방 만료 스케줄러·결과 채널은 후속 구현입니다.

## 검증

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest

# 위 로컬 서비스와 migration이 준비된 상태
uv run alembic check
uv run pytest --run-integration
```

기본 pytest는 외부 서비스 없이 단위·계약 테스트를 실행하고 integration은 명시적으로 건너뜁니다. 통합 테스트는 고유한 테스트 사용자·방·Redis 키만 생성하고 정리하며 전체 DB/Redis를 비우지 않습니다. DB·Redis는 고정된 로컬 개발 포트를 사용하고, 소켓 테스트는 임의의 빈 포트에 실제 Uvicorn 서버 2개를 실행한 뒤 정리합니다.

2026-09-08 기준 전체 66개 테스트, Ruff lint/format, mypy, migration 적용·롤백·재적용이 통과했습니다. 실제 FE 브라우저·모바일·게임 완주 검증과 원격 CI는 아직 실행하지 않았습니다.

GitHub Actions는 같은 명령과 초기 migration의 `downgrade base → upgrade head`를 격리된 CI DB에서 검사합니다. `downgrade base`는 테이블을 삭제하므로 보존할 데이터가 있는 DB에서는 실행하지 않습니다.

## 현재 범위와 문서

- [개발 계획](./PLAN.md): 구조, 계약 게이트, M0~M5 구현 순서 및 완료 기준
- [TODO 리스트](./TODO.md): 우선순위·작업 크기·요구사항별 실행 체크리스트
- [백엔드 구현 명세](../emoselfie-DOCS/development/backend/spec.md)
- [Backend Development Guidelines](../emoselfie-DOCS/development/Backend%20Development%20Guidelines.md)
- [구현 계약과 미확정 항목](./docs/contracts.md): 스키마 보강, G-01~12 상태와 후속 경계
- [요구사항 추적표](./docs/traceability.md): 구현 파일과 검증 근거

다음 개발은 라운드 상태 머신·분산 타이머·업로드·추론 통합입니다. 실제 모델 공급(G-01), 업로드 재시도·접수(G-02·03), 게임 화면 복원 DTO(G-04), 채점 동점/평균(G-11)은 아직 확정되지 않았습니다.
