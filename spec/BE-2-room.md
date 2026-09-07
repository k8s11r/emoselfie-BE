# BE-2 · room

**의존** BE-1 identity
**요구사항** RO-01 ~ RO-17, PM-07, PM-10, PM-11, 7.4
**PRD** 5.2 방 · DECISIONS C-7, C-8, C-9, C-12

---

## 목표

방을 만들고, 링크로만 입장시키고, 방장 권한을 UUID에 고정한 채 이탈·복귀를 견딘다. **한 사용자는 활성 방을 하나만 소유한다.**

---

## 규칙

### 생성과 식별자

| ID | 규칙 |
|---|---|
| RO-01 | 로그인 없이 방 생성. 초대 URL에 **최소 12자 URL-safe 난수** slug. 입력용 코드는 두지 않는다. |
| RO-17 | 생성 시 라운드 수·제한시간을 함께 지정한다. 대기실에서 시작 전까지 변경 가능. |
| C-9 | 방에는 **이름이 없다.** `Room.name` 컬럼을 두지 않는다. 대기실 헤더는 `5라운드 · 20초 · 최대 12명` 설정 요약으로 채운다. |

```python
slug = secrets.token_urlsafe(9)   # 12자
```

4자리 코드를 버린 이유: 영숫자 4자리는 약 168만 조합이고, 동시 100방 기준 임의 시도가 살아있는 방에 걸릴 확률이 1/16,800이다. 초당 10회면 평균 28분에 한 번 남의 방에 들어간다 (C-8).

설정 값:

```
roundCount    3 | 5 | 7        기본 5     (RD-01)
timeLimitSec  15 | 20 | 30     기본 20    (RD-03, RD-04)
emotionSet    easy | full      기본 full  (EM-04, P1)
```

허용값 밖은 거부한다. Client가 `timeLimitSec: 3600`을 보내도 통과시키지 않는다.

### 1인 1방

| ID | 규칙 |
|---|---|
| RO-02 | 한 UUID는 동시에 **하나의 활성 방**만 소유한다. |
| RO-03 | 활성 방이 있는 상태에서 다시 생성하면 새로 만들지 않고 **기존 방 대기실로 자동 이동**시킨다. 에러가 아니라 `200 {redirected: true, slug}`. |

DB 부분 유니크 인덱스로 강제한다. 애플리케이션 코드만으로 보장하지 않는다 (가이드 §16).

```sql
CREATE UNIQUE INDEX uq_active_room_per_host
  ON rooms (host_user_id)
  WHERE status IN ('waiting', 'playing');
```

`status = 'finished'`는 인덱스에서 빠지므로 게임이 끝나는 순간 슬롯이 자동으로 풀린다. 이것이 RO-13/FN-01의 구현이다.

### 소유 슬롯 반환

| ID | 규칙 |
|---|---|
| RO-13 | 방장은 방을 명시적으로 닫을 수 있다. 닫으면 전원에게 알리고 소유 슬롯을 즉시 반환한다. **게임이 최종 결과에 도달한 시점(FN-01)에도 자동 반환한다.** 방 레코드는 결과 조회를 위해 남고 RO-14로 소멸한다. |
| RO-14 | 마지막 활동으로부터 30분 경과 시 자동 소멸. |

자동 반환이 없으면 최종 화면의 "새 방 만들기"가 RO-03에 걸려 방금 끝난 방으로 튕긴다 (C-12).

소멸은 Redis TTL 기반 스케줄로 처리하고, 멀티 Pod에서 중복 실행되지 않도록 분산 락을 건다. `lastActiveAt`은 요청·소켓 이벤트마다 갱신한다.

### 방장 권한

| ID | 규칙 |
|---|---|
| RO-04 | 방장은 생성자 UUID로 고정. 브라우저를 닫았다 다시 와도 같은 UUID면 자동 복원. |
| RO-10 | 방장 연결이 끊겨도 방을 해체하지 않는다. **60초** 동안 자리를 비워두고 게임은 계속 진행한다. 그 안에 복귀하면 권한 그대로 복원. |
| RO-11 | 60초 후에도 안 오면 **가장 먼저 입장한 남은 참여자**에게 임시 위임. 원래 방장이 돌아오면 되돌려준다. |
| RO-12 | UUID가 달라진 사용자는 자기가 만든 방이라도 권한을 되찾을 수 없다. 일반 참여자로 입장하고 안내 문구를 띄운다. |

60초 타이머는 Redis 키 TTL + 만료 처리로 구현한다. 특정 Pod의 `asyncio.Task`에 두면 그 Pod가 죽을 때 위임이 영영 일어나지 않는다.

임시 위임 중임을 `host:changed` 페이로드의 `isTemporary`로 알린다.

### 입장

| ID | 규칙 |
|---|---|
| RO-05 | 초대 URL로만 입장. 닉네임 2~10자 지정. |
| RO-06 | 정원 **최소 2명, 최대 12명**. 가득 찬 상태면 "방이 가득 찼어요"로 거부. |
| RO-15 | 없거나 종료된 링크는 "방을 찾을 수 없어요" + 방 만들기 제안. |
| RO-09 | 게임 진행 중 입장자는 대기실에 머문다. **진행 중인 라운드의 주제·사진·점수를 일절 노출하지 않는다.** |
| RO-16 | 퇴장 후 같은 링크로 재입장하면 누적 포인트를 유지한 채 복귀 (P1). |

`GET /api/rooms/{slug}`는 입장 전 호출이므로 **존재 여부·정원·진행 여부만** 준다. 참여자 목록·닉네임·설정 상세를 여기서 내보내면 링크만 가진 외부인이 방 내용을 들여다볼 수 있다.

### 카메라 권한과 대기실

| ID | 규칙 |
|---|---|
| PM-07 | **권한이 없으면 대기실 입장 자체를 차단한다.** 방 정보·인원·라운드·사진·점수 어느 것도 볼 수 있는 경로를 두지 않는다. |
| PM-10 | 대기실에는 권한 허용자만 있으므로, 시작 버튼은 **인원 2명 이상**이면 활성화된다. |
| PM-11 | 라운드 중 권한 철회·스트림 영구 손실 시 해당 라운드를 미제출 처리하고 **방에서 내보낸다.** 누적 포인트는 보존하고, 재허용 후 같은 링크로 오면 복귀한다. |
| RO-07 | 대기실에 참여자 목록과 **연결 상태**를 실시간 표시한다. 권한 상태는 표시 대상이 아니다 (C-7). |

Backend는 카메라 권한을 직접 확인할 수 없다. FE가 `join`을 호출했다는 것이 곧 권한 허용의 증거다. 철회는 `permission:changed` (C→S)로 통보받아 PM-11을 실행한다.

**`Participant.cameraPermission` 컬럼을 두지 않는다** (v1.0에서 폐기).

### 인원 부족

남은 참여자가 2명 미만이 되면 게임을 종료하고 현재까지의 순위를 공개한다. 참여자가 1명만 남아도 같다. (PRD 10장)

---

## 데이터

```
Room
  id            UUID PK
  inviteSlug    text UNIQUE          -- 12자 URL-safe
  hostUserId    UUID FK→User
  status        enum(waiting|playing|finished)
  settings      jsonb {roundCount, timeLimitSec, emotionSet}
  createdAt     timestamptz
  lastActiveAt  timestamptz
  -- name 컬럼 없음 (C-9)
  -- UNIQUE(hostUserId) WHERE status IN (waiting, playing)

Participant
  id                UUID PK
  roomId            UUID FK→Room
  userId            UUID FK→User
  nickname          text
  colorTag          text
  connectionStatus  enum(online|offline)
  status            enum(active|waiting_next_game|left)
  totalPoints       int  DEFAULT 0
  joinedAt          timestamptz
  -- UNIQUE(roomId, userId)
  -- cameraPermission 없음 (v1.0 폐기)
```

`colorTag`는 입장 순서에 따라 팔레트에서 배정한다. 12색이 필요하다.

---

## 인터페이스

```
POST   /api/rooms                    {roundCount, timeLimitSec, emotionSet}
       → 201 {slug} | 200 {redirected: true, slug}          RO-01/02/03
GET    /api/rooms/{slug}             → 200 {exists, isFull, inProgress}  RO-06/09/15
POST   /api/rooms/{slug}/join        {nickname} → 200 {participantId, ...}
PATCH  /api/rooms/{slug}/settings    방장 · waiting 상태만                RO-17
POST   /api/rooms/{slug}/start       방장 · 2명 이상                      RO-08, PM-10
POST   /api/rooms/{slug}/close       방장                                 RO-13
POST   /api/rooms/{slug}/leave                                            RO-16
GET    /api/rooms/{slug}/snapshot    재접속 복원                          ID-05
```

이벤트: `room:joined` `host:changed` `room:closed` `participant:updated` `participant:removed` `permission:changed`

---

## 테스트

- slug 길이 ≥ 12, URL-safe, 충돌 시 재생성
- 활성 방 보유 상태에서 재생성 → `redirected: true` + 기존 slug
- DB 레벨에서 동시 생성 2건 → 하나만 성공 (부분 유니크 인덱스)
- `status='finished'` 전환 직후 새 방 생성 성공 (RO-13/FN-01/C-12)
- 방 닫기 → 전원 `room:closed`, 슬롯 즉시 반환
- 13번째 입장 거부, 12번째는 성공
- 종료된 slug 접속 → 404 계열 안내
- 게임 중 입장 → `waiting_next_game`, 라운드 이벤트 미수신
- 방장 disconnect → 60초 내 복귀 시 권한 유지
- 방장 disconnect → 60초 초과 시 최선입장자에게 위임, `isTemporary: true`
- 임시 위임 후 원 방장 복귀 → 권한 반환
- 쿠키 삭제 후 자기 방 입장 → 일반 참여자
- `permission:changed` 수신 → 미제출 처리 + 퇴장 + 포인트 보존 (PM-11)
- 인원 2명 미만 → 게임 종료 + 현재 순위 공개
- `lastActiveAt` 갱신 없이 30분 → 소멸
- `GET /api/rooms/{slug}` 응답에 참여자 닉네임·설정 상세가 없음

---

## 하지 않는 것

```
❌ 방 이름 컬럼·입력 (C-9)
❌ 4자리 입장 코드 (C-8)
❌ 대기실에 권한 미허용자 존재 (PM-07)
❌ Participant.cameraPermission 보관
❌ 애플리케이션 코드만으로 1인1방 보장 (DB 제약 필수)
❌ 방장 60초 타이머를 특정 Pod 메모리에 보관
❌ 입장 전 API에서 방 내용 노출
❌ socket disconnect = 즉시 퇴장 처리
```
