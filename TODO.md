# 이모셀피 Backend TODO

- 작성일: 2026-09-08
- 기준: [개발 계획](./PLAN.md), [Backend spec](../emoselfie-DOCS/development/backend/spec.md), [PRD](../emoselfie-DOCS/requirements.md)
- 상태: `[ ]` 미완료, `[x]` 구현·검증 완료. 2026-09-08 M0 기반 구현 착수. 세부 증거는 [추적표](./docs/traceability.md)를 따른다.
- 우선순위: P0 MVP 필수, P1 후속. 크기: S 반나절 안팎, M 1~2일, L 3일 이상으로 분할 필요. 계약/모델 공급 대기와 베타 기간은 제외한 상대 추정이다.
- `§`는 Backend spec 절 번호다. 의존성은 단계 제목과 G-ID로 표시한다. 완료 체크 시 PR·테스트 결과를 항목 아래에 기록한다.

## 0. 계약 확정 — M0부터 관련 구현 전까지

각 항목의 상세 근거와 제안은 PLAN §3을 따른다. 담당은 개인 미배정 상태이며 괄호의 역할은 협의 대상이다. 결정 기록에 선택안·근거·영향 API·fixture·확정일을 남긴다.

- [ ] `BE-DEC-01` (P0/M, 모델·BE·Infra) G-01 모델 아티팩트·아키텍처·라벨·전처리·버전·체크섬·공급 경로 확보 — M1 전, §21-A
- [ ] `BE-DEC-02` (P0/M, BE·FE) G-02 토글/업로드 재시도·중복 제거·접수 조회·오류 우선순위 확정 — M1/M2 전, FE B-3·4
- [ ] `BE-DEC-03` (P0/M, BE·FE·Infra) G-03 접수 예약·body 제한·실패 정리·viewers 인정·scoring guard 관계 확정 — M1 전
- [ ] `BE-DEC-04` (P0/L, BE·FE) G-04 screen DTO·revision/재전달·backlog·내 명령 상태·서버 순서 fixture 합의 — M2 전, FE B-1·2·5·11
- [ ] `BE-DEC-05` (P0/M, BE·FE·제품) G-05 입장 전 필드·닉네임 규칙·12색 재사용·참여자 추가 및 mutation 응답 합의 — M2 전, FE B-7·8·10
- [ ] `BE-DEC-06` (P0/M, BE·FE·제품) G-06 신규/기존/left/finished·카메라 실패·권한 철회·제출 후 퇴장 정책 및 RO-16 MVP 포함 여부 합의 — M2/M3 전, FE B-6·9
- [ ] `BE-DEC-07` (P0/M, BE·FE·제품) G-07 감상 시간·스킵 단계/분모/방장 권한·제출 0건·마지막 무효 라운드 확정 — M2/M3 전, FE B-12, §21-B·C
- [ ] `BE-DEC-08` (P0/M, BE) G-08 ID 할당·missed/Reaction/RoundSkip 저장 시점·finalized/closed 커밋 경계 확정 — M2 전
- [ ] `BE-DEC-09` (P0/L, BE·Infra) G-09 잡 유실·커밋 후 이벤트 유실·Redis 유실·임시 방장 복구 설계 확정 — M2 설계, M3 검증
- [ ] `BE-DEC-10` (P0/M, BE·Infra·FE) G-10 이미지 Redis 격리·persistence 금지·서명 만료/갱신·프록시 buffering 계약 및 사진 삭제 고지 정합성 확인 — M1/M5, FE Q-1
- [ ] `BE-DEC-11` (P0/S, BE·제품) G-11 failed 평균 모집단·반올림·완전 동점·no_face 정렬 fixture 확정 — M2 전
- [ ] `BE-DEC-12` (P0/S, BE·제품) G-12 idle 활동·종료/삭제 기준 시각·보존 기간·P1 정리 범위 확정 — M2 전

## 1. 기반 — M0

선행: 없음. 계약 의존 부분은 합의한 fixture로 고정한다.

- [x] `BE-001` (P0/M) Python 3.11 프로젝트, pyproject/lock, §2.2 모듈 구조와 의존 방향 구성; 재현 가능한 설치 명령 README 기록
  - 2026-09-08: `uv sync --locked`, lint/format/typecheck 로컬 검증. PR 미생성.
- [x] `BE-002` (P0/S) §20 Pydantic Settings·검증·비밀값 없는 `.env.example`, UTC clock 및 epoch-ms 직렬화 구현
  - 2026-09-08: `tests/unit/test_core.py` 검증. 개발 키는 init_env.py로 별도 생성.
- [x] `BE-003` (P0/M) 공통 AppError/HTTP envelope/Socket ack·§15 코드와 한국어 문구·ID 문자열·null 규약 구현
  - 2026-09-08: `tests/contract/test_envelopes.py`, `test_core.py` 검증. 실제 소켓 핸들러는 BE-030 이후.
- [x] `BE-004` (P0/M) 로컬 PostgreSQL·조율 Redis·이미지 Redis 환경 및 async 연결/수명주기 구성
  - 2026-09-08: Compose 실서비스, readiness/의존성 오류/종료 정리 통합 검증.
- [x] `BE-005` (P0/L) §4의 7개 테이블·ENUM·CHECK·UNIQUE·부분 인덱스·FK/CASCADE migration 작성; current_round_id 정합성과 삭제 순서 검증 — G-08·12
  - 2026-09-08: migration 0001 적용/롤백/재적용 및 alembic check 검증. DB 제약·동시 활성 방 생성·CASCADE 테스트. G-08 저장 시점과 G-12 삭제 잡 정책은 여전히 미확정.
- [ ] `BE-006` (P0/M) §5 키 생성기·TTL·트랜잭션 경계·소유 토큰 기반 잠금 해제 구성; tempHost 등 누락 키도 목록화 — G-09·10
  - 2026-09-08 부분 구현: 키/TTL 상수·SET NX PX/소유 토큰 해제·실제 Redis 경합 테스트. 도메인 TTL 원자적 쓰기와 tempHost 복구 정책은 후속.
- [x] `BE-007` (P0/M) FakeClassifier·고정 clock·DB/Redis fixture 및 pytest unit/integration/contract 기반, lint/format/typecheck CI 구성
  - 2026-09-08: 로컬 전체 테스트와 품질 검사 검증, CI workflow 작성. GitHub 원격 CI 실행은 미실시.
- [ ] `BE-008` (P0/M) HTTP·소켓 정상/오류 JSON fixture와 screen별 DTO 저장, 요구사항→구현→테스트 추적표 골격 작성 — G-02~07
  - 2026-09-08 부분 구현: 오류/소켓 성공 ack fixture·추적표. screen DTO와 HTTP 자원 성공 fixture는 계약 결정 후 작성.

완료 게이트: 빈 DB migration, 로컬 의존성 연결, fake 기반 테스트 및 품질 명령이 재현 가능하다.

## 2. 업로드·추론·이미지 — M1

선행: M0, G-01·02·03·10. 세션/참여자 검증은 BE-020~024와 통합한다. M1 단일 사용자 검증은 개발 fixture로 수행하고 운영 게임의 최소 2명 규칙은 유지한다.

- [x] `BE-010` (P0/M) EmotionClassifier Protocol·EmotionResult·InferenceError 경계와 fake 시나리오 구현; no_face와 failed 구분 — §12.1, SC-04·05
  - 2026-09-08: 성공/무검출/실패/취소 가능한 timeout·7확률 검증 테스트. 실제 엔진은 미구현.
- [x] `BE-011` (P0/L) 실제 아키텍처·weight·FaceDetector startup 로드/warmup·모델 누락 시 실패·종료 자원 해제 — §12.4, G-01
  - 2026-09-08: Ryumina ResNet50 AffectNet weight와 MediaPipe BlazeFace를 sha256·크기로 고정하고 `scripts/prepare_models.py`로 공급한다. 로드·warmup은 스레드에서 수행해 이벤트 루프를 막지 않으며, 아티팩트 누락·크기·체크섬 불일치는 startup 실패로 처리하고 fake로 대체하지 않는다. `tests/model` 및 `test_inference.py`로 검증. 모델 담당의 공급 경로 승인(BE-DEC-01)은 여전히 미완료다.
- [x] `BE-012` (P0/L) JPEG decode→최대 얼굴→crop→224×224→BGR→VGGFace2 mean→ResNet50→7확률 구현; `/255`·ImageNet 정규화 금지, 모델 기준 출력 대조 — §12.2
  - 2026-09-08: 전처리를 upstream `pth_processing` 기준 구현과 교차 계산해 오차 없이 일치함을 확인했다(`test_preprocessing_matches_the_upstream_reference`). 저자 데모 이미지는 happy 0.97로 판정되어 라벨 순서를 고정했다. 얼굴 crop은 upstream의 face_mesh 랜드마크 박스가 아니라 BlazeFace 검출 박스를 사용하므로 crop 좌표까지 동일하지는 않다.
- [x] `BE-013` (P0/L) 큐·Semaphore(초기 2)·전용 executor·5초 timeout·서킷 브레이커(최근 20건 80% 실패 시 60초) 구현; 실제 작업 종료 전 슬롯 해제로 동시성이 초과되지 않는지 검증 — §12.3
  - 2026-09-08: timeout·취소 시에도 네이티브 작업이 끝날 때까지 슬롯을 유지하고, 버려진 요청은 실행하지 않으며, 종료는 대기 작업을 거절하고 진행 중 작업을 배수한 뒤 엔진을 닫는다. 취소된 요청이 서킷을 오염시키지 않음도 확인했다(`test_runner.py`). 업로드 API 연결은 BE-014·016에서 이어진다.
- [ ] `BE-014` (P0/L) body 읽기 전 서버 수신 시각 기록, 방/참여자/현재 라운드/상태 검증, deadline·개인 촬영 토큰·원자적 중복 접수 구현 — §6.3·8.3, CP-03·08~10, G-02·03
- [ ] `BE-015` (P0/L) multipart 스트림 2MB 한도·Content-Length 없는 요청·실제 decode 검증·body 중단/413/415 정리 구현; 임시 파일 방지 및 비정상 이미지 열람 차단 — §8.3, G-03·10
  - 2026-09-08 부분 구현: `media/images.py`의 바이트·픽셀 한도, JPEG 시그니처·다중 프레임·decompression bomb 거절, EXIF 회전 및 임시 파일 미사용, 결과 재인코딩 시 메타데이터 제거를 검증했다. multipart 스트림 한도와 body 중단 정리는 업로드 엔드포인트(BE-014)와 함께 구현한다.
- [ ] `BE-016` (P0/M) 202/processing·submissionId·acceptedAtMs 반환, 재요청 기존 접수 복원·큐 투입 실패 처리 구현 — §8.3, G-02·08
- [ ] `BE-017` (P0/L) 전체 프레임 JPEG 재인코딩·Redis TTL 180초·개인 mediaToken 발급/갱신·쿠키 소유/현재 열람 권한 확인·no-store·403/410 구현; Q-7 추가 반전/크롭 없음 — §8.4, PV-01·02, G-10
- [ ] `BE-018` (P0/M) 실제 모델 단일 사용자 업로드→결과 benchmark, 모델/장비·동시성·큐 대기·메모리 기록; 원본/crop 해제·no_face/오류·timeout 검증 — §21-D
  - 2026-09-08 부분 측정(Apple M3, 8 CPU, torch 1스레드, Python 3.11.16): 로드 1.41초·warmup 0.04초, 프로세스 RSS 47MB→454MB. 단일 추론 p50 24ms(375×375)~30ms(1440×1920). 1440×1920 12장 동시 제출은 concurrency 2에서 254ms에 완료됐다. no_face와 자원 해제는 `tests/model`로 검증했다. HTTP 업로드 구간을 포함한 종단 측정은 BE-014·016 이후로 남는다.

완료 게이트: 실제 JPEG가 실제 모델 결과로 연결되고 자원이 정리된다. 모델 미확보 시 M1 완료로 표시하지 않는다.

## 3. 세션·방·참여 — M2

선행: M0, G-05·06·12. M1 모델 작업과 독립적으로 시작 가능하다.

- [x] `BE-020` (P0/M) es_uid UUID/HMAC 쿠키 발급·위변조 재발급·속성·이전 키 검증 및 GET/PATCH `/api/me` 구현; UUID 비노출 — §6.1·8.1, ID-01~06·08
  - 2026-09-08: security 단위 테스트 및 room_api의 실제 쿠키 위변조·키 교체·닉네임 복원·응답 비노출 검증. 닉네임 세부 구현 규칙은 docs/contracts.md에 기록.
- [x] `BE-021` (P0/M) 12자 URL-safe slug·방 생성 201/기존 200·활성 소유 방 부분 UNIQUE 경합 처리 — §8.2, RO-01~03
  - 2026-09-08: 같은 사용자 동시 4회 요청에서 방 1개·201 한 번·200 세 번 검증. 닫기/finished 이후 새 슬롯 생성 검증.
- [ ] `BE-022` (P0/M) 입장 전 최소 조회·ROOM_NOT_FOUND/CLOSED/FINISHED 계약과 정보 필터 구현 — §6.4·8.2·15, PM-07, G-05
  - 2026-09-08: §8.2와 현 FE parser의 5필드 구현 및 비참여자 정보 필터 테스트. §6.4의 3필드 제한과 충돌하는 G-05 최종 합의는 미완료.
- [ ] `BE-023` (P0/L) 입장 멱등성·정원 12명 원자적 검사·닉네임/색상·active/waiting_next_game 처리; 신규/복원 경로 분리 — §3.2·8.2, RO-05·06·09, G-05·06
  - 2026-09-08: 14명 동시 입장·중복 입장·left 정원 재검사/색상 재배정·포인트 보존 구현. G-05·06의 제품 계약 완료는 아님.
- [ ] `BE-024` (P0/M) current_participant/require_host·임시 방장 권한 경계, waiting 전용 설정 3/5/7·15/20/30·full 고정 구현 — §6·8.2·10.7, RO-17
  - 2026-09-08: 원 방장·현재 참여자 권한·waiting 설정 및 소켓 반영 검증. 임시 방장 미구현.
- [x] `BE-025` (P0/M) active 2명 이상 시작·7감정 비복원 시퀀스 저장·중복 start 방어 — §9·10, RD-01·02, PM-10
  - 2026-09-08: §9.1 감정 마스터 테이블과 §9.2 시퀀스, `POST /start`, 1라운드 생성, `game:started` 브로드캐스트와 참여자별 `round:revealed`·촬영 토큰을 구현했다. 2개 Pod에서 방장 권한·2명 미만 거절·중복 start 409·설정 동결·진행 중 입장자의 waiting_next_game을 검증했다. 라운드 진행(마감·채점)은 BE-033·034·036이다.
- [ ] `BE-026` (P0/M) 방 닫기·finished 소유 슬롯 즉시 반환·결과 조회 보존·이미지/잡/소켓 정리 구현 — §3.1·10.6, RO-13, FN-01
  - 2026-09-08: 대기 방 닫기·슬롯 반환·room:closed 및 소켓 해제만 구현. 게임/이미지/잡 정리는 후속.
- [ ] `BE-027` (P0/M) 활동 시각·idle 30분 만료·종료 방 30분 보존 후 CASCADE 삭제 잡 구현; 재접속/핑/재예약 경합 검증 — §4.3·10.4, RO-14, G-12

## 4. 실시간·게임 루프 — M2

선행: BE-020~025, G-02~09. 채점 순수 함수(BE-040)는 먼저 착수 가능하다.

- [ ] `BE-030` (P0/L) AsyncServer+AsyncRedisManager ASGI 조립·쿠키/참여자 인증·room/player/viewer/개인 채널과 단일 emitter 구현 — §13.1, ID-08, RS-12
  - 2026-09-08: ASGI·RedisManager·서명 쿠키/현재 참여자 인증·room/player/개인 채널·대기실 emitter 구현. 결과 viewer 채널은 미구현.
- [x] `BE-031` (P0/L) UUID별 유효 sid 원자적 교체·이전 Pod 소켓 superseded/disconnect·늦은 disconnect가 새 연결을 지우지 않도록 검증 — §6.2, ID-07
  - 2026-09-08: 실제 Uvicorn 2개·공유 Redis/PG·Socket.IO 클라이언트로 교체/이전 서버 늦은 disconnect 검증. 장애 중 DB/Redis 재조정은 BE-057 후속.
- [ ] `BE-032` (P0/M) room:joined·settingsUpdated·participant 갱신·game:started·presence 25초와 상태 DTO 구현; 대기자의 라운드 정보 차단 — §13, G-05·06
  - 2026-09-08: 대기실 전체 snapshot 갱신·presence ack/TTL 구현. game:started 및 게임 중 상태는 미구현.
- [ ] `BE-033` (P0/L) Room/Round CAS와 revealed→capturing→scoring→finalized/voided→closed 구현; 3초 카운트다운·개인 captureToken·절대 deadline 발행 — §3·10.1, RD-03·08
  - 2026-09-08 부분 구현: 방 행 잠금과 상태 CAS로 revealed→scoring→finalized/voided→closed 전이, 3초 카운트다운·절대 deadline·개인 촬영 토큰 발행을 구현했다. capturing은 별도 서버 전이 없이 클라이언트가 countdownEndsAt으로 계산하며, DB에는 업로드 경로(BE-014)가 붙을 때 기록한다.
- [ ] `BE-034` (P0/L) ZSet 250ms 스케줄러·원자적 claim·잠금·재실행/복구 구현; deadline/scoring_guard/viewing_end/participant_left/host_delegate/room_expire 잡 등록 — §10.4, G-09
  - 2026-09-08 부분 구현: ZSet 폴링·Lua ZREM 소유권·30초 잡 락·취소/재예약과 Pod별 루프를 구현하고, 실제 Redis에서 동시 claim 1회·핸들러 실패 격리·중복 발화 없음을 검증했다. deadline·scoring_guard·viewing_end를 등록한다. participant_left·host_delegate·room_expire와 G-09의 잡 유실 복구는 미구현이다.
- [ ] `BE-035` (P0/L) 유효 제출 인정→viewers 가입·제출 수 알림·기존 결과 backlog, 비동기 scored 개인 토큰 발행 구현 — §8.3·13, RD-05·09, RS-01~05·09, G-03·04
- [ ] `BE-036` (P0/L) deadline/전원 제출 경쟁 시 scoring 1회 전환·missed 확정·missed→missedUpdate 2단계 알림 구현 — §10.2·13, RD-06·07, RS-11~14
  - 2026-09-08 부분 구현: deadline 잡의 scoring 1회 전환, 미제출자 확정과 `round:missed`·`round:missedUpdate` 2단계 알림을 구현했다. 알림은 남은 시간과 단계만 담는다. 전원 제출 조기 마감은 업로드 접수(BE-014)와 함께 붙인다.
- [ ] `BE-037` (P0/M) 마감+8초 guard·늦은 추론 결과 폐기·전원 failed 무효·연속 3회 중단·정상 라운드 카운터 초기화·서킷 대기 구현 — §10.3·10.5, D-5
  - 2026-09-08 부분 구현: 마감+8초 guard가 미확정 추론을 failed로 확정하고, 제출자 전원 failed면 라운드를 무효로 만들며 연속 카운터를 증가·정상 라운드에서 초기화한다. 늦은 결과 폐기와 서킷 브레이커 대기는 업로드 경로 이후다.
- [ ] `BE-038` (P0/M) 제출 0건·마지막 voided·active 2명 미만 종료·남은 잡 취소와 모든 종료 경로 처리 — §10.5·10.6, G-07
  - 2026-09-08 부분 구현: 제출 0건은 감상 단계를 건너뛰고, 마지막 voided도 정상 종료로 처리하며, active 2명 미만과 연속 무효 3회는 중단 종료로 이어진다. 남은 guard·viewing_end 잡을 취소한다. G-07 합의 전 BE 기준이며 docs/contracts.md에 기록했다.
- [ ] `BE-039` (P0/L) GET `/state` screen별 snapshot·serverTimeMs·제출/토큰/결과/내 리액션·스킵 복원·구독 경합/재전달 구현; 미제출자 감정까지 제거 — §14, ID-05, G-04·06
  - 2026-09-08: waiting snapshot·현재 참여자 권한·DB 행 잠금으로 일관된 읽기 구현. 게임 상태는 503으로 차단하며 screen/revision/replay는 미구현.

## 5. 채점·리액션·결과 — M2

선행: G-07·08·11, 결과 통합은 BE-030~039 및 M1 엔진 필요.

- [x] `BE-040` (P0/M) targetScore 1자리·라운드 정렬·100/70/50/30·no_face 30·failed 보정·missed 0 순수 함수와 §18.1 테이블 테스트 구현 — SC-01~05·09, G-11
  - 2026-09-08: `domain/scoring`에 라운드 채점·누적·최종 순위·mostLoved를 I/O 없는 순수 함수로 구현하고 §18.1 표 10개 케이스를 모두 덮었다. 반올림 half-up, failed 평균 모집단, 0점과 no_face 정렬, 완전 동점 키는 docs/contracts.md에 BE 기준으로 기록했으며 G-11 합의로 바뀔 수 있다. DB 반영과 이벤트 발행은 BE-041·042다.
- [ ] `BE-041` (P0/L) finalized 트랜잭션에 제출·포인트·best_round_score·라운드 상태 반영, 재실행 시 중복 누적 방지 및 커밋 후 이벤트 복구 — D-8, G-08·09
  - 2026-09-08 부분 구현: 제출·포인트·best_round_score·라운드 상태를 한 트랜잭션에 반영하고, scoring 상태 CAS로 재실행 시 중복 누적을 막는다. 커밋 후 이벤트 유실 복구는 G-09와 함께 남는다.
- [ ] `BE-042` (P0/M) 최종 순위(-totalPoints,-bestRoundScore,joinedAt)·mostLoved/동수/null, 중단 reason 및 전원 game:finished 구현 — §11.4, SC-06, RX-11, FN-01~03
  - 2026-09-08 부분 구현: 최종 순위·mostLoved·중단 reason과 전원 `game:finished` 발행, 종료 시 소유 슬롯 반환을 구현했다. 리액션 누적은 아직 0이므로 mostLoved는 항상 null이며 BE-043 이후 실제 값이 된다.
- [ ] `BE-043` (P0/L) 리액션 수신 권한·채점 확정·자기 사진 금지·like/question 독립 토글·원자적 카운트·중복 명령 방어 구현 — §13.2, RX-01~09, G-02
- [ ] `BE-044` (P0/M) 감상 시간 min(60,10+viewers×2)·서버 종료 시각·스킵 토글·connected viewers 분모·방장 즉시 종료 구현 — §10.3, RS-08·15, G-07
- [ ] `BE-045` (P0/L) 종료/투표 경합 직렬화·closed 리액션 최종 DB 저장·집계/종료 snapshot·종료 후 명령 거부 구현 — RX-10, G-08
- [ ] `BE-046` (P0/M) 정상/무효/강제 종료마다 img 키 즉시 삭제·토큰 무효·viewers 정리; TTL 이전에도 `/media` 410 검증 — PV-01·02
- [ ] `BE-047` (P0/L) 결과 REST/이벤트/media 공통 권한 테스트: 비참여자·대기자·미제출자·퇴장자·타 방 사용자·토큰 교차 사용 차단 — PM-07, RS-12, G-03·06·10
- [ ] `BE-048` (P0/M) FE B-1~12 정상/오류 fixture contract test 및 늦은 열람자 backlog·전체 순서·processing 상태 검증
- [ ] `BE-049` (P0/L) 실제 FE/BE 2인·3라운드 E2E: 시작→사진→채점→리액션/스킵→미제출→최종 결과→새 방 생성 검증

M2 완료 게이트: 실제 모델로 2인 완주, 결과 접근제어·누적 무결성·초기 복원 및 FE 계약 테스트 통과.

## 6. 재접속·예외·다중 Pod — M3

선행: M2. 장애 재현에는 실제 PostgreSQL/Redis와 2개 ready 인스턴스를 사용한다.

- [ ] `BE-050` (P0/M) disconnect 60초 후 left·정원 반환·active 인원 재판정, 10초 내 재접속 보존·오래된 유예 잡 무효화 — D-6, ID-05
- [ ] `BE-051` (P0/L) 방장 60초 부재→최초 active 참여자 임시 위임·연쇄 위임·원 방장 복귀·쿠키 삭제 일반 참여자 처리 — RO-10~12
- [ ] `BE-052` (P0/L) permission:changed denied→미제출/퇴장·포인트 보존·소켓/미디어 권한 회수·복귀 정책 구현 — PM-11, G-06
- [ ] `BE-053` (P0/L) 모든 screen에서 reload/재접속·finished/left 복원·이벤트 유실/역전/재전달·동일 UUID 탭 경쟁 통합 검증 — §14·18, G-04·06
- [ ] `BE-054` (P0/L) deadline -1ms/정각/+1ms·느린 body·중복 토큰/업로드·업로드 실패·전원 제출과 deadline 동시성 테스트 — CP-03·08~10
- [ ] `BE-055` (P0/M) 동시 방 생성/정원 마지막 슬롯/동시 start·토글/전원 스킵/종료 경합 테스트 — RO-02·06, RX-02~04, RS-15
- [ ] `BE-056` (P0/L) 2 Pod에서 교차 이벤트/개인 media 열람·상태 전이 효과 1회·추론/잡 처리 중 Pod kill 검증 — §18.3
- [ ] `BE-057` (P0/L) DB 커밋 후 emit 실패·Redis/PG 단절·진행 라운드 Redis 유실·tempHost 소실 복구 테스트; 이전 확정 점수 보존 검증 — D-8, G-09
- [ ] `BE-058` (P0/M) §18.1~18.3 및 §19 요구사항→구현 파일→테스트 추적표 완성, P0 누락·P1 혼입 점검

## 7. 보안·관측·베타·출시 — 기반은 M0, 최종 검증 M4~M5

선행: 보안/로그는 관련 API와 함께 적용한다. 실제 베타는 M3, 출시 검증은 M4 이후다.

- [ ] `BE-060` (P0/M) Redis 토큰 버킷: 생성 IP 10/h·user 5/h, 조회 IP 30/min, 입장 IP 20/min, 업로드 participant 5/round, socket sid 30/10s 및 Retry-After — §16
  - 2026-09-08: 방 생성/조회/입장 및 presence 소켓 명령의 원자적 토큰 버킷 구현. 업로드 및 후속 소켓 명령은 미연결.
- [ ] `BE-061` (P0/M) 구조화 로그 allowlist·UUID/이미지/base64/crop/face_box/토큰 차단·오류 경로 필터 테스트 — §17, ID-08, PV-05
- [ ] `BE-062` (P0/M) §17의 추론/접수/확정/소켓/잡 지연/이미지 메모리 메트릭·대시보드/알림과 종단 성능 측정 연결
- [ ] `BE-063` (P0/M) Docker·모델 RO mount·환경변수/Secret·1 worker·live/ready·graceful shutdown·CI 이미지 빌드 및 migration 실행 절차 — §8.5·20
  - 2026-09-08 확인: 현재 lock은 Linux에서 torch의 CUDA 휠과 nvidia 패키지 34개를 함께 해석한다. 서버는 CPU만 사용하므로 이미지 크기와 CI 시간을 위해 `download.pytorch.org/whl/cpu` 인덱스를 Linux에 한정해 고정하고 lock을 재생성해야 한다. 그 뒤에 `--run-model`을 실행하는 CI 잡을 추가한다.
- [ ] `BE-064` (P0/L) Infra와 동일 오리진 TLS·Socket.IO 다중 Pod 연결 방식·프록시 수신 시각/body buffering·임시 파일·Redis 별도 인스턴스/persistence/메모리 상한 검증 — G-10, PM-14, PV-05
- [ ] `BE-065` (P0/L) FE와 iOS Safari/Android Chrome 실제 기기, Wi-Fi↔LTE·background·권한 철회·2명/12명 검증; 오프라인 모임 3회 이상 베타 기록 — M4, §21-E
- [ ] `BE-066` (P0/L) 100방/1,200명·동시 제출·지연/오류/재접속 부하 측정; 업로드 완료→결과 p95≤3초·상태 편차≤500ms·메모리/큐 상한 검증 — M5, §21-D·F
- [ ] `BE-067` (P0/M) 모델 공급/버전 교체·rollback·2키 30일 쿠키 회전·의존성 장애/정리·과부하 대응 runbook 작성·재현 — §21-G
- [ ] `BE-068` (P0/M) P0 전체 체크·실제 모델/FE/2 Pod/부하 증거·알려진 제한·P1 보류 목록과 릴리스 준비 기록 작성

## 8. P1 후속

P0 완료와 별도로 관리한다. 앞당겨 구현할 때에는 범위 결정과 우선순위 변경을 기록한다.

- [ ] `BE-P1-01` (P1/M) 자발적 DELETE participants/me·동일 UUID 재입장·누적 유지 구현/검증 — RO-16, G-06; PM-11 P0 복귀 처리와 공통 도메인 사용
- [ ] `BE-P1-02` (P1/M) 1년 미접속 User 일일 정리·참조/FK/활성 방 예외 검증 — ID-09, §4.3
- [x] `BE-P1-03` (P1/S) hard 감정 비인접 셔플 최대 20회·실패 시 진행 — EM-03
  - 2026-09-08: §9.2가 시퀀스 생성 알고리즘 안에서 함께 규정하므로 BE-025와 같이 구현했다. 우선순위 변경이 아니라 같은 순수 함수의 5줄이며, 20회 실패 시 그대로 진행해 게임을 막지 않는다.
- [ ] `BE-P1-04` (P1/M) easy 5감정 설정·7라운드 조합 거부 — EM-04
- [ ] `BE-P1-05` (P1/M) 베타 승인 시 포인트 A/B 전략 및 지표 설계; MVP 배점 자동 변경 없음 — §21
