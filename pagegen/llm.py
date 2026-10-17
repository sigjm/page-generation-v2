"""MLX Serve client (spec §2, §6). Chat, tools, vision and json_schema go through LangChain's ChatOpenAI;
the Qwen-Image edit uses non-standard OpenAI fields, so it keeps a plain stdlib HTTP call."""
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

import openai
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageOps

MLX_URL = os.environ.get("MLX_URL", "http://127.0.0.1:11234")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "empero-ai/Qwen3.8-9B-Distill-MLX-4bit")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
IMAGE_STEPS = int(os.environ.get("IMAGE_STEPS", "20"))
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", TEXT_MODEL)  # the claim judge can run on its own fine-tuned model
COPY_MODEL = os.environ.get("COPY_MODEL", TEXT_MODEL)  # the first-draft copy can run on its own fine-tuned model
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


def chat_model(*, thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0,
               model: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(base_url=f"{MLX_URL}/v1", api_key="local", model=model or TEXT_MODEL, temperature=temperature,
                      max_tokens=max_tokens, timeout=300, max_retries=0,
                      extra_body={"chat_template_kwargs": {"enable_thinking": thinking}})


def _openai_calls(message) -> list[dict]:
    """AIMessage tool calls back in OpenAI form. Calls whose arguments were not valid JSON keep their raw text,
    so the agent can hand the model its "인자 JSON" error instead of losing the call."""
    calls = [{"id": c.get("id") or "", "type": "function",
              "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
             for c in message.tool_calls]
    calls += [{"id": c.get("id") or "", "type": "function",
               "function": {"name": c.get("name") or "", "arguments": c.get("args") or ""}}
              for c in message.invalid_tool_calls]
    return calls


def _lc_message(message: dict):
    """An assistant turn with tool calls as an AIMessage. LangChain json.loads every arguments string in a dict
    history, so a call the model sent with broken JSON goes in as an invalid tool call, raw text and all."""
    if message.get("role") != "assistant" or not message.get("tool_calls"):
        return message
    valid, invalid = [], []
    for call in message["tool_calls"]:
        function = call.get("function") or {}
        raw = function.get("arguments") or ""
        try:
            args = raw if isinstance(raw, dict) else json.loads(raw)
        except ValueError:
            args = None
        if isinstance(args, dict):
            valid.append({"name": function.get("name") or "", "args": args, "id": call.get("id") or ""})
        else:
            invalid.append({"name": function.get("name") or "", "args": raw if isinstance(raw, str) else json.dumps(raw),
                            "id": call.get("id") or "", "error": None})
    return AIMessage(content=message.get("content") or "", tool_calls=valid, invalid_tool_calls=invalid)


def chat(messages: list[dict], *, tools: list[dict] | None = None, schema: dict | None = None,
         thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0, model: str | None = None) -> dict:
    """One chat completion. Returns {"content", "tool_calls"} with OpenAI-form tool calls (arguments as JSON text)."""
    runnable = chat_model(thinking=thinking, max_tokens=max_tokens, temperature=temperature, model=model)
    if tools:
        runnable = runnable.bind_tools(tools, tool_choice="auto")
    if schema:
        runnable = runnable.with_structured_output({"title": "out", **schema}, method="json_schema", strict=True,
                                                   include_raw=True)
    try:
        reply = runnable.invoke([_lc_message(m) for m in messages])
    except openai.APIStatusError as e:
        raise LLMError(f"모델 서버 오류: {e.status_code} {e.response.text[:200]}") from e
    except Exception as e:  # noqa: BLE001 — any malformed reply (200 with an error body, no choices…) is a model failure
        raise LLMError(f"모델 서버 오류: {e}") from e
    if isinstance(reply, dict):  # structured output: chat_json parses the raw text itself (fences, retries)
        reply = reply["raw"]
    content = reply.content if isinstance(reply.content, str) else ""
    if "</think>" in content:
        content = content.rpartition("</think>")[2]
    elif content.lstrip().startswith("<think>"):
        content = ""  # thinking cut off by max_tokens: there is no answer, and the reasoning is never shown
    return {"content": content.strip(), "tool_calls": _openai_calls(reply)}


def parse_json(text: str | None) -> dict:
    data = json.loads(_FENCE.sub("", (text or "").strip()))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def chat_json(messages: list[dict], schema: dict, *, max_tokens: int = 1500, model: str | None = None) -> dict:
    """json_schema call, thinking off; one retry at temperature 0.3 (spec §4.1, §5)."""
    error: Exception | None = None
    for temperature in (0.0, 0.3):
        content = None
        try:
            content = chat(messages, schema=schema, max_tokens=max_tokens, temperature=temperature, model=model).get("content")
            return parse_json(content)
        except (LLMError, ValueError) as e:
            log.warning("JSON retry: %s | tail=%r", e, (content or "")[-160:])
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
