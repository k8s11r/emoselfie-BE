# BE-4 · inference

**의존** 없음 — 게임 도메인을 모른다. BE-1~3과 **병렬 개발 가능**
**요구사항** EM-01, SC-04, SC-05, SC-07, PV-01, PV-05, PRD 10장
**가이드** §4 ~ §7, §18 ~ §21, §25
**PoC 원본** `~/project/p1/femo.py` · `FEMO_WEB.md` (전처리·라벨의 정본)

---

## 목표

JPEG 한 장을 받아 7개 감정 확률과 얼굴 검출 여부를 돌려준다. **그게 전부다.** 방도 라운드도 점수도 모른다.

PRD 마일스톤 M1(단일 사용자 프로토타입)이 정확히 이 모듈이다. 다른 모듈이 없어도 완성할 수 있다.

---

## 라벨 매핑 — 제일 먼저 확인할 것

모델은 숫자 7개를 뱉는다. **그 순서는 PRD 5.4 표의 나열 순서와 다르다.**

```python
# femo.py:52 — 저자 Space app/config.py DICT_EMO(0~6)와 일치 확인됨
LABELS = ["Neutral", "Happiness", "Sadness", "Surprise", "Fear", "Disgust", "Anger"]
```

| 인덱스 | 모델 라벨 | 내부 키 | 표시 이름 |
|---|---|---|---|
| 0 | `Neutral` | `neutral` | 시크 |
| 1 | `Happiness` | `happy` | 기쁨 |
| 2 | `Sadness` | `sad` | 슬픔 |
| 3 | `Surprise` | `surprise` | 놀람 |
| 4 | `Fear` | `fear` | 무서움 |
| 5 | `Disgust` | `disgust` | 우웩 |
| 6 | `Anger` | `angry` | 분노 |

PRD 표의 나열 순서(`happy sad angry surprise neutral disgust fear`)와 비교하면 **7개 중 5개가 어긋난다.** 우연히 맞는 것은 인덱스 3(surprise)과 5(disgust)뿐이다.

**`EmotionResult.probabilities`는 반드시 라벨을 키로 하는 dict다.** 배열이나 인덱스로 게임 영역에 넘기지 않는다. 인덱스로 넘기는 순간, 누군가 PRD 표 순서로 zip하면 오류 없이 점수만 전부 틀린다.

이 버그는 조용하다. 서버는 200을 내고, 점수는 소수점까지 그럴듯하고, 로그도 깨끗하다. 게다가 이 제품은 "판정이 빗나가는 것 자체가 재미"(PRD 1.3)라 베타 테스터가 웃고 넘어간다. **제품 컨셉이 버그를 위장한다.**

### 기동 시 검증 (EM-01)

모델 로드 직후 라벨 배열이 기대값과 같은지 확인하고, 다르면 **서버를 띄우지 않는다.**

```python
EXPECTED_LABELS = ("Neutral","Happiness","Sadness","Surprise","Fear","Disgust","Anger")
# 불일치 → startup 실패. 모델 교체 시 매핑을 안 고치는 경우를 잡는다
```

---

## 경계

게임 로직이 PyTorch 구현 세부에 의존하지 않도록 Protocol로 막는다 (가이드 §5).

```python
class EmotionResult(BaseModel):
    face_detected: bool
    probabilities: dict[str, float] | None   # 내부 키(happy/sad/...) → 0~1. 배열 금지
    face_box: tuple[int, int, int, int] | None
    model_version: str
    elapsed_ms: int

class EmotionClassifier(Protocol):
    async def classify(self, image: bytes) -> EmotionResult: ...
```

게임 코드가 아는 것은 이 두 가지뿐이다.

```
이미지 → EmotionClassifier → EmotionResult
```

게임 코드에서 직접 호출하지 않는 것:

```
torch.load()   MediaPipe   softmax()   model(...)
```

`app/inference/`는 `app/domain/`을 import 하지 않고, 그 반대도 아니다.

---

## 파이프라인

```
Uploaded JPEG
      ↓  Image Decode        (Pillow, .convert("RGB"))
      ↓  Face Detection      (MediaPipe Tasks FaceDetector, blaze_face_short_range)
      ↓  Largest Face Selection   ← width × height 최대
      ↓  Face Crop
      ↓  resize (224, 224)   Image.Resampling.NEAREST
      ↓  PILToTensor         uint8 0~255 유지
      ↓  float32 cast
      ↓  torch.flip(x, dims=(0,))        RGB → BGR
      ↓  채널별 평균 감산    (91.4953, 103.8827, 131.0912)
      ↓  unsqueeze(0)
      ↓  ResNet50(7, channels=3)  →  logits
      ↓  softmax(dim=1)
      ↓  7 Emotion Probabilities
```

위 값은 전부 PoC `femo.py`의 실측값이다. 추정이 아니다.

**전처리를 임의로 바꾸지 않는다.** 조용히 점수를 흔드는 함정 세 개:

```
❌ /255 normalization              PILToTensor 대신 ToTensor를 쓰면 자동으로 붙는다
❌ ImageNet mean/std normalization
❌ 리사이즈 필터 변경               NEAREST → BILINEAR/BICUBIC 로 바꾸면 값이 달라진다
```

`ToTensor()`는 0~1 float를 내고 `PILToTensor()`는 0~255 uint8을 낸다. 평균 감산값이 0~255 스케일 기준이므로 **반드시 `PILToTensor()`** 를 쓴다. 가이드 §6의 "/255 금지"가 여기서 나온 규칙이다.

리사이즈 필터가 `NEAREST`인 것은 저자 Space를 그대로 따른 것이다. 보통은 쓰지 않는 선택이라 "개선"하고 싶어지는데, 그 순간 모델이 학습한 입력 분포에서 벗어난다.

전처리는 모델 버전의 일부다. 모델을 바꾸면 전처리도 같이 버전 관리한다 (가이드 §6).

### 얼굴 검출

| 상황 | 처리 |
|---|---|
| 미검출 | `face_detected=False`, 감정 모델을 **호출하지 않는다** → 호출부에서 `NO_FACE` → 0점 (SC-04) |
| 다중 검출 | 첫 번째가 아니라 **bounding box 면적(`w × h`)이 가장 큰 얼굴** 선택 (PRD 10장) |

얼굴이 없는데 원본 이미지를 그대로 감정 모델에 넣는 것은 금지다 (가이드 §28). 배경이나 손이 "분노 92점"으로 나온다.

### 실패 구분

호출부가 두 가지를 구분할 수 있어야 한다 (가이드 §30).

| 결과 | 의미 | 게임 처리 |
|---|---|---|
| `face_detected=False` | 얼굴 없음 | 0점, "얼굴을 찾지 못했어요" (SC-04) |
| 예외 / 타임아웃 | 처리 실패 | **판정 불가**, 0점이 아니고 총점 페널티 없음 (SC-05) |

이 구분이 무너지면 SC-04와 SC-05가 섞여 사용자가 억울해진다.

| ID | 규칙 |
|---|---|
| SC-07 | 판별 응답이 **5초**를 초과하면 타임아웃 처리. |

---

## 모델 로딩

| 규칙 | 근거 |
|---|---|
| Pod startup에서 **한 번** 로드한다. request마다 로드하지 않는다. | §18 |
| 로드 → FaceDetector 로드 → **warmup** → ready | §18, §24 |
| 첫 사용자 요청에서 모델을 다운로드하거나 로드하는 구조를 쓰지 않는다. | §18 |
| 모델 파일이 없으면 **서버 startup을 실패시킨다.** | §19 |
| Application은 모델을 다운로드할 책임이 없다. | §19 |

```
Pod Start → Model Load → FaceDetector Load → Warmup → Ready
```

### Artifact

Docker image에 포함: Backend source, Python deps, PyTorch, MediaPipe, **모델 아키텍처 코드**.
Volume에서 공급: `*.pt`, `*.tflite`.

```
/models/
├── emotion/
│   └── v1/
│       └── FER_static_ResNet50_AffectNet.pt
└── face/
    └── blaze_face_short_range.tflite
```

```
EMOTION_MODEL_VERSION=v1
EMOTION_MODEL_PATH=/models/emotion/v1/model.pt
FACE_MODEL_PATH=/models/face/blaze_face_short_range.tflite
```

Backend 버전과 모델 버전을 같이 묶지 않는다. Backend `v1.4.2` + Emotion Model `v2` 조합이 정상이다 (§21). 모델 교체 때문에 Backend image를 다시 설계하지 않도록 한다.

`EmotionResult.model_version`을 항상 채운다. 점수 이상을 추적할 때 어느 모델이 냈는지 알아야 한다.

---

## 동시성

PyTorch·MediaPipe 연산을 FastAPI event loop에서 무제한 병렬 실행하지 않는다 (§25). MediaPipe detector의 thread safety와 CPU 자원을 고려한다.

```
Inference Request → Semaphore → FaceDetector → PyTorch
```

- 블로킹 연산은 `run_in_executor`로 내보내 event loop를 막지 않는다.
- 동시 실행 수는 `INFERENCE_CONCURRENCY` 환경변수. **정확한 값은 benchmark로 결정한다. 임의로 높게 잡지 않는다.**
- 세마포어 대기 시간도 SC-07의 5초 예산에 포함된다.

이 모듈이 event loop를 막으면 Socket.IO 이벤트 전파가 함께 멈춘다. 7.2의 "클라이언트 간 편차 500ms"가 여기서 깨진다.

---

## 프라이버시

| ID | 규칙 |
|---|---|
| PV-01 | **영구 저장소에 기록하지 않는다.** 판별에 쓰인 원본 바이트는 처리 직후 메모리에서 해제한다. 결과 화면용 임시 캐시는 BE-5가 관리한다. |
| PV-05 | 전 구간 암호화. **로그에 이미지 원본이나 식별 가능한 파생물을 남기지 않는다.** |

```
HTTP Body → Memory → Decode → Face Detection → Inference → Result → Memory release
```

임시 파일도 필요 없으면 쓰지 않는다. 로그 금지 대상: 이미지 원본, base64, 얼굴 crop, 식별 가능한 파생 데이터.

디버깅용으로 crop을 저장하고 싶어질 때가 온다. 그때가 이 규칙이 필요한 순간이다.

---

## femo_web.py 취급

`femo_web.py`는 서비스 Backend 코드가 아니라 **PoC**다. Production에서 직접 쓰지 않는다.

```
❌ Gradio UI          ❌ femo_web.predict()
❌ matplotlib chart   ❌ Gradio monkeypatch
❌ Gradio lazy singleton   ❌ URL 입력 기능
```

PoC에서 승계하는 것은 **검증된 기술적 사실**뿐이다: 모델 종류, 7개 라벨, 얼굴 crop 필요성, MediaPipe FaceDetector 사용 가능 여부, preprocessing 방식.

PoC 위치: `~/project/p1/femo.py` (핵심 파이프라인) · `femo_web.py` (Gradio 래퍼) · `FEMO_WEB.md` (정리 문서).

`femo.py`의 `load_image` / `detect_and_crop_face` / `build_model` / `preprocess`는 Gradio 의존성이 없어 그대로 옮길 수 있다. `femo_v1.py`는 얼굴 검출이 없는 최초 버전이므로 **참조하지 않는다.**

### PoC를 그대로 옮기면 안 되는 4가지

| PoC 동작 | Production | 근거 |
|---|---|---|
| `result.detections[0]` — **첫 번째** 얼굴 | **면적 최대** 얼굴 | PRD 10장, 가이드 §7 |
| 얼굴 미검출 시 **원본 전체로 fallback** | `NO_FACE` → 0점 | 가이드 §28이 명시적 금지 |
| `hf_hub_download()`로 **기동마다** 아키텍처 `.py`와 weights를 원격 수신, `exec_module`로 실행 | 아키텍처 코드는 이미지에 vendoring, weights는 PV | 가이드 §19 + 원격 코드 실행 위험 |
| lazy singleton `get_model()` | startup 로드 + warmup + ready | 가이드 §18, §24 |

`FEMO_WEB.md` §10도 같은 4건을 지적하고 있다.

추가로:

- `torch.load()`에 **`weights_only=True`** 를 붙인다. PoC는 붙이지 않아 pickle 역직렬화 위험이 있다.
- MediaPipe `FaceDetector`는 **스레드 세이프가 보장되지 않는다** (`FEMO_WEB.md` §10). 세마포어 + 락, 또는 요청별 인스턴스를 검토한다.
- PoC의 Tasks API 사용은 저자 원본(레거시 Solutions API `face_mesh`)의 대체다. crop 영역이 미세하게 다르지만 분류 결과에는 문제 없음이 실측 확인됐다.

### 착수 전 환경 검증

PoC는 **Python 3.9.6 / mediapipe 0.10.35 / torch 2.8.0 / torchvision 0.23.0**에서 검증됐다. BE 스택은 **Python 3.11**이다.

`FEMO_WEB.md`가 "mediapipe는 0.10.35 고정 권장, 1.0.x에서 Tasks API 동작 미검증"이라고 못박아 뒀다. **BE-4 착수 첫 작업은 Python 3.11 + mediapipe 0.10.35 조합에서 `femo.py`가 같은 결과를 내는지 확인하는 것이다.** 여기서 막히면 Python 버전이나 얼굴 검출 방식을 다시 정해야 한다.

검증 기준값: 저자 Space `images/fig1.jpg` → **Top-1 `Happiness` (0.970)**

---

## 테스트

고정 이미지 픽스처를 `tests/fixtures/`에 둔다. **사람 얼굴 픽스처는 저작권·초상권이 확인된 것만 쓴다.**

- 얼굴 1개 정면 → `face_detected=True`, 확률 합 ≈ 1.0, 7개 라벨 모두 존재
- 얼굴 없음(풍경) → `face_detected=False`, 감정 모델 **미호출** (mock으로 호출 횟수 검증)
- 얼굴 2개 크기 다름 → 큰 쪽 `face_box` 선택
- 손상된 JPEG → 예외, `NO_FACE`와 구분되는 실패
- 5초 초과 → 타임아웃 (SC-07)
- 전처리 단위 테스트: 224×224 **NEAREST**, `PILToTensor`(0~255), BGR 순서, mean `(91.4953, 103.8827, 131.0912)` 감산, `/255`·ImageNet 정규화 **미적용**
- **라벨 배열이 `EXPECTED_LABELS`와 일치** — 불일치 시 startup 실패 (EM-01)
- `probabilities`가 dict이며 키가 내부 키(`happy` 등)다. 배열이 아니다
- 웃는 얼굴 픽스처 → `happy` 최대. **`neutral`이 최대로 나오면 인덱스 오매핑**
- 저자 Space `images/fig1.jpg` → Top-1 `happy` ≈ 0.97 (회귀 기준값)
- 모델 파일 없음 → startup 실패
- warmup 완료 전 `ready` 실패
- 세마포어 한도 초과 요청이 event loop를 막지 않는다
- 전체 테스트 실행 후 로그에 base64·이미지 바이트가 없다

`slow` 마크로 실제 모델 로딩 테스트를 분리해 기본 실행에서 제외한다.

---

## 하지 않는 것

```
❌ 게임 도메인 import
❌ request마다 모델 로드
❌ runtime에 모델 다운로드
❌ 얼굴 미검출 시 원본으로 감정 판별
❌ 첫 번째 detection 무조건 사용 (PoC가 그렇게 되어 있다)
❌ /255 · ImageNet 정규화 추가 · ToTensor 사용
❌ 리사이즈 필터를 NEAREST 외로 변경
❌ probabilities 를 배열·인덱스로 게임 영역에 전달
❌ 라벨 순서를 PRD 표의 나열 순서로 가정
❌ 기동 시 hf_hub_download 로 아키텍처·weights 수신
❌ 이미지·crop·base64 로깅
❌ 임시 파일에 이미지 기록
❌ 무제한 동시 inference
❌ Gradio · femo_web 의존성 반입
```
