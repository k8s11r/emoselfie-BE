# 감정 판별 PoC

`femo_web.py`는 **서비스 코드가 아니다.** 사용할 감정 인식 모델과 처리 방식을 검증하기 위해 작성한 PoC이며, 참고 목적으로만 보관한다.

[가이드라인 §4](../../guidelines.md)가 이 파일에 대한 원칙을 정한다. Production Backend에서 다음을 직접 사용하지 않는다.

```
❌ Gradio UI          ❌ femo_web.predict()
❌ matplotlib chart   ❌ Gradio monkeypatch / lazy singleton
❌ URL 입력 기능
```

PoC에서 가져오는 것은 **검증된 기술적 사실**뿐이다 — 모델 종류, 7개 emotion label, 얼굴 crop 필요성, MediaPipe FaceDetector 사용 가능 여부, preprocessing 방식. Production 추론 코드는 `app/inference/`에 별도로 작성되어 있다.

## 실행되지 않는다

이 파일은 이 저장소의 환경에서 동작하지 않는다. 의도된 상태다.

- `gradio`, `matplotlib`이 이 프로젝트 의존성에 없다.
- 26행의 `from femo import LABELS, build_model, detect_and_crop_face, load_image, preprocess`가 참조하는 `femo` 모듈이 저장소에 없다. [spec.md §21-A](../../spec.md), [PLAN.md의 G-01](../../PLAN.md)에서 추적 중인 미확보 항목이다.

`app/inference/pipeline.py`의 `PreprocessingSpec`은 정확한 mean·interpolation 값을 `femo.py`에서 받아야 한다고 명시하고 있다. 그 값이 확정되기 전까지 이 PoC는 전처리 규칙의 유일한 근거다.

## 검사 대상에서 제외

`pyproject.toml`의 ruff `extend-exclude`에 `docs/poc`가 들어 있다. PoC의 코드 스타일을 서비스 코드 기준으로 교정하면 검증 시점의 원본과 달라지므로 lint·format을 적용하지 않는다. mypy는 `app`만 검사하므로 애초에 대상이 아니다.
