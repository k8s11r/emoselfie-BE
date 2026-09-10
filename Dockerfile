# syntax=docker/dockerfile:1.7
#
# 이 이미지는 linux/amd64 전용이다. MediaPipe가 linux/aarch64 휠을 배포하지
# 않으므로 arm64로는 빌드할 수 없다. Apple Silicon에서는 Rosetta로 실행된다.
#
# 모델 가중치는 이미지에 넣지 않는다. spec §20의 기본 경로에 read-only로
# 마운트한다. 파일이 없으면 startup이 실패한다(가이드라인 §19).

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG UV_VERSION=0.12.10

# ─────────────────────────────── 의존성 설치 ───────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock ./

# --extra inference 가 torch와 mediapipe를 가져온다.
# uv.lock은 x86_64 linux에서 CUDA 휠을 함께 해석하므로 이미지가 커진다.
# CPU 인덱스 고정은 BE-063의 작업이며 lock 재생성이 필요하다.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra inference

# ───────────────────────── 모델 아티팩트 준비 ─────────────────────────
# K8s의 init container와 같은 역할이다. 백엔드보다 먼저 실행되어 모델을
# 볼륨에 놓고 끝난다. 백엔드 이미지와 분리하는 이유는 두 가지다.
#   · 운영 이미지에 다운로드 클라이언트를 넣지 않는다 (가이드라인 §28)
#   · 스크립트는 httpx를 쓰는데 이는 dev 의존성이라 런타임에 없다
# 스크립트는 이미 멱등하다. 파일이 있고 sha256이 맞으면 받지 않는다.
FROM ${PYTHON_IMAGE} AS models

RUN pip install --no-cache-dir httpx==0.28.1

WORKDIR /app
COPY app/inference/artifacts.json ./app/inference/artifacts.json
COPY scripts/prepare_models.py ./scripts/prepare_models.py

ENTRYPOINT ["python", "scripts/prepare_models.py"]
CMD ["--directory", "/models"]

# ───────────────────────────────── 런타임 ─────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

# MediaPipe의 C 바인딩이 GL/GLES 공유 라이브러리를 dlopen 한다.
# libgles2가 libGLESv2.so.2를 제공하며, 이것이 없으면 FaceDetector 생성이
# OSError로 실패해 startup이 죽는다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgles2 libegl1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY alembic.ini ./
COPY app ./app

RUN useradd --system --uid 10001 --create-home appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Socket.IO 연결이 살아 있으면 Uvicorn의 graceful shutdown이 끝나지 않는다.
# BE-063에 기록된 사항이라 타임아웃을 반드시 지정한다.
CMD ["uvicorn", "app.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--timeout-graceful-shutdown", "10"]
