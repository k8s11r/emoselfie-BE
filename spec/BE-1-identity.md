# BE-1 · identity

**의존** 없음 (BE-7 ops 골격만 전제)
**요구사항** ID-01 ~ ID-09, RO-12, 7.4
**PRD** 5.1 사용자 식별 · 가이드 §14, §15

---

## 목표

로그인 없이 사용자를 식별한다. 방장 권한과 재접속 복원이 전부 이 식별자 하나에 달려 있으므로, **위조 불가**와 **복원 가능**을 동시에 만족해야 한다.

---

## 규칙

### 발급과 쿠키

| ID | 규칙 |
|---|---|
| ID-01 | 최초 접속 시 서버가 UUID v4를 발급해 쿠키로 내려준다. 이후 모든 요청·소켓 연결의 사용자 식별자다. |
| ID-02 | 쿠키 속성 `HttpOnly` `Secure` `SameSite=Lax`, `Max-Age=31536000`(1년). |
| ID-03 | 권한이 걸린 동작은 쿠키 값만 믿지 않는다. 서버가 `User ↔ Room` 소유 관계를 조회해 검증한다. |
| ID-06 | 쿠키가 없거나 삭제되면 **새 사용자**다. 이전 방의 방장 권한은 승계하지 않는다 (RO-12). |
| ID-08 | UUID를 개인정보와 연결하지 않는다. **클라이언트로 나가는 실시간 이벤트에는 UUID 대신 `participantId`를 싣는다.** |

쿠키 이름은 `esf_uid`. **[가정]**

FE JavaScript가 UUID를 읽을 수 있다는 전제로 코드를 쓰지 않는다 (가이드 §14). FE가 아는 것은 자기 `participantId`뿐이다.

### 닉네임

| ID | 규칙 |
|---|---|
| ID-04 | 닉네임은 User(UUID)에 붙여 보관한다. 재접속 시 입력란 기본값으로 채운다. 매번 변경 가능. |

검증: 2~10자 (RO-05). 앞뒤 공백 제거, 연속 공백 1개로 축약, 제어문자·zero-width 제거. **[가정]**
같은 방 내 중복 허용 여부는 PRD 13장 미결 2번 — **MVP는 중복 허용**하고 색상 태그로 구분한다. **[가정]**

### 세션 복원

| ID | 규칙 |
|---|---|
| ID-05 | 새로고침·재접속 시 쿠키 UUID로 기존 Participant에 복귀한다. 게임 진행 중이면 현재 라운드 상태와 누적 포인트를 복원한다. |

복원 페이로드는 `GET /api/rooms/{slug}/snapshot`이 담당하지만, "이 UUID가 어떤 Participant인가"를 해석하는 책임은 이 모듈에 있다.

일시적 socket disconnect만으로 Participant/Room 데이터를 삭제하지 않는다 (가이드 §15).

### 다중 연결

| ID | 규칙 |
|---|---|
| ID-07 | 같은 UUID로 여러 탭·기기에서 접속하면 **마지막 연결만 유효**하고 이전 연결을 종료한다. |

구현: Redis에 `conn:{userId} → sid`를 두고, 새 연결이 붙으면 이전 `sid`에 종료 사유를 보내고 `disconnect`한다. 한 사람이 여러 창으로 중복 제출·득점하는 것을 막는 장치다.

> 시크릿 모드·다른 브라우저는 별개 UUID이므로 별개 사용자로 취급된다. 1인 1방 제약이 우회되지만 PRD가 수용 가능한 한계로 명시했다.

### 정리

| ID | 규칙 | 우선순위 |
|---|---|---|
| ID-09 | 마지막 접속 후 1년 경과한 User 레코드를 정기 삭제한다. | P1 |

MVP에서는 `lastSeenAt`만 정확히 갱신해 두고 삭제 배치는 P1로 미룬다.

---

## 데이터

```
User
  uuid        UUID  PK
  nickname    text  NULL
  createdAt   timestamptz
  lastSeenAt  timestamptz   -- 요청·소켓 활동마다 갱신
```

개인정보를 담는 컬럼을 추가하지 않는다.

---

## 인터페이스

```
GET /api/session
  → 200 { userId?: never, nickname: string | null, isNew: boolean }
    Set-Cookie: esf_uid=...  (없던 경우)
```

응답에 UUID 자체를 넣지 않는다 (ID-08).

FastAPI 의존성:

```python
async def current_user(request: Request) -> User: ...        # 없으면 발급
async def current_participant(...) -> Participant: ...        # 방 컨텍스트에서
async def require_host(...) -> Participant: ...               # ID-03 검증
```

Socket.IO `connect` 핸들러는 handshake 쿠키에서 UUID를 읽어 Participant로 해석하고, 실패하면 연결을 거부한다.

---

## 테스트

- 쿠키 없이 접속 → UUID 발급, `Set-Cookie` 속성 3종 확인
- 같은 쿠키로 재접속 → 같은 User
- 쿠키 삭제 후 자기 방 접속 → 새 User, 방장 아님 (RO-12)
- 응답·이벤트 페이로드 어디에도 UUID 문자열이 없음 (ID-08)
- 같은 UUID 2개 소켓 → 먼저 붙은 쪽 종료 (ID-07)
- 닉네임 1자·11자 거부, 공백만 거부
- 활동 시 `lastSeenAt` 갱신

---

## 하지 않는 것

```
❌ 로그인·회원가입·비밀번호
❌ UUID를 응답 body나 이벤트에 노출
❌ 쿠키 존재만으로 방장 판정
❌ JWT 등 별도 토큰 체계 도입 (MVP 범위 밖)
❌ socket disconnect 시 Participant 즉시 삭제
```
