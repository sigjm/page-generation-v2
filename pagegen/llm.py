"""MLX Serve client (spec §2, §6): chat with tools / vision / json_schema, and Qwen-Image edit. stdlib HTTP."""
import base64
import http.client
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

MLX_URL = os.environ.get("MLX_URL", "http://127.0.0.1:11234")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "empero-ai/Qwen3.8-9B-Distill-MLX-4bit")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
IMAGE_STEPS = int(os.environ.get("IMAGE_STEPS", "20"))
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
log = logging.getLogger("pagegen")


class LLMError(Exception):
    """The model server failed; the message is shown to the user."""


def _post(route: str, body: dict, timeout: float = 300) -> dict:
    request = urllib.request.Request(MLX_URL + route, json.dumps(body, ensure_ascii=False).encode(),
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        raise LLMError(f"모델 서버 오류: {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (OSError, http.client.HTTPException, ValueError) as e:  # also a server dying mid-response
        raise LLMError(f"모델 서버 오류: {e}") from e


def chat(messages: list[dict], *, tools: list[dict] | None = None, schema: dict | None = None,
         thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0) -> dict:
    body = {"model": TEXT_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    if tools:
        body |= {"tools": tools, "tool_choice": "auto"}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}
    try:
        message = _post("/v1/chat/completions", body)["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"모델 응답 형식 오류: {e}") from e
    content = message.get("content")
    if content:
        if "</think>" in content:
            content = content.rpartition("</think>")[2]
        elif content.lstrip().startswith("<think>"):
            content = ""  # thinking cut off by max_tokens: there is no answer, and the reasoning is never shown
        message["content"] = content.strip()
    return message


def parse_json(text: str | None) -> dict:
    data = json.loads(_FENCE.sub("", (text or "").strip()))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def chat_json(messages: list[dict], schema: dict, *, max_tokens: int = 1500) -> dict:
    """json_schema call, thinking off; one retry at temperature 0.3 (spec §4.1, §5)."""
    error: Exception | None = None
    for temperature in (0.0, 0.3):
        try:
            return parse_json(chat(messages, schema=schema, max_tokens=max_tokens, temperature=temperature).get("content"))
        except (LLMError, ValueError) as e:
            log.warning("JSON retry: %s", e)
            error = e
    raise LLMError(f"모델 응답을 두 번 받지 못했습니다: {error}")


def _upright_rgb(file: Path) -> Image.Image:
    """EXIF rotation applied; transparency flattened onto white (cut-out PNGs would turn black)."""
    with Image.open(file) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            canvas = Image.new("RGB", im.size, "white")
            canvas.paste(im, mask=im.getchannel("A"))
            return canvas
        return im.convert("RGB")


def _jpeg(im: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    im.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def model_jpeg(file: Path, max_side: int = 1280) -> bytes:
    im = _upright_rgb(file)
    im.thumbnail((max_side, max_side))
    return _jpeg(im, 90)


def edit_input(file: Path, size: int = 1024) -> bytes:
    """Aspect-preserving contain on a white square, so the server never has to crop or stretch (spec §6)."""
    im = ImageOps.contain(_upright_rgb(file), (size, size))
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    return _jpeg(canvas, 95)


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def edit_image(file: Path, instruction: str) -> bytes:
    body = {"model": IMAGE_MODEL, "prompt": f"{instruction}. 제품의 모양, 색상, 로고와 무늬는 그대로 보존한다.",
            "mode": "edit", "image": base64.b64encode(edit_input(file)).decode(), "size": "1024x1024",
            "steps": IMAGE_STEPS, "strength": 0.3, "response_format": "b64_json"}
    try:
        return base64.b64decode(_post("/v1/images/generations", body, timeout=600)["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError(f"이미지 응답 형식 오류: {e}") from e
