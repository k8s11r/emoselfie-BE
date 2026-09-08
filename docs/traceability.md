# 요구사항 → 구현 → 검증

2026-09-08 M0, 세션·방·실시간 대기실, 실제 추론 엔진 범위. 표의 기반 완료는 해당 제품 요구사항 전체 완료를 의미하지 않는다.

| TODO / 근거 | 구현 | 검증 | 상태 |
|---|---|---|---|
| BE-001, §2 | pyproject.toml, uv.lock, app 모듈 | Python 3.11 설치, lint/typecheck | 완료 |
| BE-002, §2.3·20 | core/config.py, clock.py, .env.example | test_core.py | 완료 |
| BE-003, §7·15 | core/errors.py, schemas.py, api/errors.py | test_envelopes.py, test_core.py | 완료 |
| BE-004, §5·8.5 | compose.yaml, db/session.py, core/resources.py, api/health.py | test_health.py, test_redis.py | 로컬 기반 완료 |
| BE-005, RO-02·RD-08·SC-01·RX-02·03 | db/models.py, migrations/0001 | test_database.py, alembic check, downgrade/upgrade | 스키마 완료; 게임 트랜잭션 후속 |
| BE-006, §5 | core/redis.py | test_redis.py | 키·상수·소유 토큰 해제 구현; 도메인 TTL 쓰기 및 G-09 후속 |
| BE-007, §18 | tests/, CI workflow, FakeClassifier | unit/contract/integration 전체 | 기반 완료; GitHub 원격 실행 미실시 |
| BE-008, §7·13·14 | tests/fixtures/errors.json, 이 추적표 | test_envelopes.py | 부분 완료; 화면/성공 HTTP fixture 미확정 |
| BE-010, SC-04·05, §12.1 | inference/protocol.py, fake.py | test_inference.py | 경계·fake 완료; 실제 채점 통합 후속 |
| BE-011, §12.4, G-01 | inference/loader.py, backends.py, artifacts.json, scripts/prepare_models.py | test_real_model.py, test_inference.py | 로드·warmup·해제 완료; 공급 경로 승인 대기 |
| BE-012, §12.2 | inference/pipeline.py, vendor/ryumina.py | test_pipeline.py, test_real_model.py의 upstream 전처리 대조 | 완료; crop 전략은 upstream과 다름 |
| BE-013, §12.3 | inference/runner.py | test_runner.py | 완료; 업로드 API 연결은 BE-014·016 |
| BE-015 일부, §8.3 | media/images.py | test_images.py | 부분 완료; multipart 스트림·body 중단은 미구현 |
| BE-018, §21-D | inference/runner.py, pipeline.py | 로컬 benchmark 측정 기록 | 엔진 구간만 측정; 종단 측정 미실시 |
| BE-040, SC-01~05·09, §11·18.1 | domain/scoring/service.py | test_scoring.py | 순수 함수 완료; G-11 합의와 BE-041·042 반영 후속 |
| BE-020, ID-01~06·08 | core/security.py, api/middleware.py, api/session.py | test_security.py, test_room_api.py | 세션 완료 |
| BE-021, RO-01~03 | domain/room/service.py, api/rooms.py | 동시 4회 생성/슬롯 반환 테스트 | 완료 |
| BE-022~024, PM-07·RO-05·06·17 | domain/room, api/rooms.py | 비참여자 차단·14명 경합·재입장·방장 설정 | REST 구현; G-05·06·임시 방장 후속 |
| BE-026 | api/rooms.py, realtime/server.py | 대기 방 닫기·소켓 종료 | 게임 종료·잡/이미지 정리는 후속 |
| BE-030~032, ID-07·08 | realtime/server.py, emitter.py | test_realtime.py의 실제 서버 2개 | 대기실/중복 연결 구현; viewer·game 이벤트 후속 |
| BE-039, §14 | domain/room/service.py, api/rooms.py | waiting snapshot·권한 테스트 | 게임 screen/replay 미구현 |
| BE-060, §16 | core/ratelimit.py, api/deps.py | 429·Retry-After 및 소켓 ping | 방 API·presence 연결; 업로드 후속 |
| BE-063, §8.5 | api/health.py, main.py | ready 성공/실패·자원 해제 | 부분 착수; 이미지/운영 배포 미구현 |

검증 명령은 [README](../README.md)를 따른다. 두 Uvicorn 서버 간 대기실·소켓 교체는 실제 네트워크로 검증했다.
실제 모델은 고정한 아티팩트로 로컬에서 로드·전처리 대조·추론까지 검증했으나 업로드 API에는 아직 연결하지 않았다.
전체 게임의 다중 Pod 복구, 제품 E2E, FE 브라우저 연동, 부하·모바일 베타는 실행하지 않았으며 이 결과로 완료 판정하지 않는다.

2026-09-08 로컬 검증: Python 3.11.16, PostgreSQL 16.13, Redis 7.4.8, torch 2.8.0, mediapipe 0.10.35.
단위·계약·통합·모델 합계 131개 통과(단위·계약 98, 통합 26, 모델 7). Ruff lint/format, mypy,
`alembic check`, 초기 migration 적용→롤백→재적용 통과. 원격 PR/CI 결과는 아직 없다.

BE-018 로컬 benchmark(Apple M3, 8 CPU, torch 1스레드, INFERENCE_CONCURRENCY=2):
모델 로드 1.41초, warmup 0.04초, RSS 47MB→454MB, 12장 동시 처리 후 최대 464MB.
단일 추론 p50 24ms(375×375)·26ms(960×1280)·30ms(1440×1920), p95 최대 39ms.
1440×1920 12장 동시 제출은 254ms(장당 21ms)에 완료됐다. 5초 timeout과 §21-D의
업로드→결과 p95 3초 목표에 견주면 엔진 구간은 여유가 있으나, HTTP 업로드·Redis 저장·
이벤트 전달을 포함한 종단 측정은 아직 아니다. 실제 배포 장비와 동시 방 부하는 M5에서 다시 측정한다.
