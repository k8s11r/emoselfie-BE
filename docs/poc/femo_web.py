"""
femo.py의 얼굴 crop + 감정 분석 파이프라인을 로컬 웹 페이지로 실행

사용법:
    python femo_web.py
    (브라우저에서 http://127.0.0.1:7860 접속)

필요 패키지 (femo.py 의존성 + 아래 추가):
    pip install gradio matplotlib
"""

import io
import time

import matplotlib

matplotlib.use("Agg")
import gradio.networking
import gradio_client.utils as gc_utils
import httpx
import matplotlib.pyplot as plt
import gradio as gr
import torch
from PIL import Image

from femo import LABELS, build_model, detect_and_crop_face, load_image, preprocess


def _url_ok_no_proxy(url: str) -> bool:
    """gradio.networking.url_ok 대체본 (원본과 동일하게 재시도 포함).

    launch() 시작 시 gradio가 httpx로 로컬 서버에 자체 접속을 확인하는데,
    macOS 시스템 프록시 자동설정을 httpx/urllib이 읽다가
    "argument of type 'bool' is not iterable" 로 깨지는 경우가 있다.
    trust_env=False로 프록시 환경설정 조회 자체를 건너뛴다.
    """
    for _ in range(5):
        try:
            r = httpx.head(url, timeout=3, verify=False, trust_env=False)
            if r.status_code in (200, 401, 302):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


gradio.networking.url_ok = _url_ok_no_proxy


# gradio_client 1.3.0의 JSON-Schema→Python-type 변환기가 boolean 스키마
# (예: additionalProperties: true)를 처리하지 못해 "argument of type 'bool'
# is not iterable"로 크래시하는 업스트림 버그 우회.
_orig_json_schema_to_python_type = gc_utils._json_schema_to_python_type


def _safe_json_schema_to_python_type(schema, defs):
    if isinstance(schema, bool):
        return "Any"
    return _orig_json_schema_to_python_type(schema, defs)


gc_utils._json_schema_to_python_type = _safe_json_schema_to_python_type

_model = None


def get_model():
    global _model
    if _model is None:
        _model = build_model()
    return _model


def make_bar_chart(labels, probs):
    ranked = sorted(zip(labels, probs), key=lambda x: x[1], reverse=True)
    labels_sorted = [r[0] for r in ranked]
    probs_sorted = [r[1] for r in ranked]

    fig, ax = plt.subplots(figsize=(5, 3))
    y_pos = range(len(labels_sorted))
    ax.barh(y_pos, probs_sorted, color="#4C78A8")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels_sorted)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("probability")
    for i, v in enumerate(probs_sorted):
        ax.text(min(v + 0.01, 0.95), i, f"{v:.3f}", va="center")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf)


def predict(mode, upload_img, url_text):
    if mode == "파일 업로드":
        if upload_img is None:
            raise gr.Error("이미지를 업로드해주세요.")
        img = upload_img.convert("RGB")
    else:
        if not url_text or not url_text.strip():
            raise gr.Error("이미지 URL을 입력해주세요.")
        try:
            img = load_image(url_text.strip())
        except Exception as e:
            raise gr.Error(f"이미지 로드 실패: {type(e).__name__}: {e}")

    face = detect_and_crop_face(img)
    warning = ""
    if face is None:
        warning = "⚠️ 얼굴을 검출하지 못했습니다. 원본 이미지 전체로 분석합니다."
        face = img

    tensor = preprocess(face)
    model = get_model()
    with torch.no_grad():
        logits = model(tensor)
    probs = torch.softmax(logits, dim=1)[0].tolist()

    chart = make_bar_chart(LABELS, probs)
    top_label, top_prob = max(zip(LABELS, probs), key=lambda x: x[1])

    result_lines = [f"### Top-1: **{top_label}** ({top_prob:.3f})"]
    if warning:
        result_lines.insert(0, warning)
    result_md = "\n\n".join(result_lines)

    return face, chart, result_md


def toggle_inputs(mode):
    is_upload = mode == "파일 업로드"
    return gr.update(visible=is_upload), gr.update(visible=not is_upload)


with gr.Blocks(title="얼굴 감정 분석") as demo:
    gr.Markdown("# 얼굴 감정 분석 (7종)\n이미지를 업로드하거나 URL을 입력하세요.")

    mode = gr.Radio(
        ["URL", "파일 업로드"], value="URL", label="입력 방식"
    )
    upload_img = gr.Image(type="pil", label="이미지 업로드", visible=False)
    url_text = gr.Textbox(
        label="이미지 URL", placeholder="https://...", visible=True
    )
    mode.change(toggle_inputs, inputs=mode, outputs=[upload_img, url_text])

    submit_btn = gr.Button("분석 시작", variant="primary")

    with gr.Row():
        crop_out = gr.Image(label="검출된 얼굴 (Crop)")
        chart_out = gr.Image(label="감정 확률 분포 (내림차순)")

    result_out = gr.Markdown()

    submit_btn.click(
        predict,
        inputs=[mode, upload_img, url_text],
        outputs=[crop_out, chart_out, result_out],
    )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)
