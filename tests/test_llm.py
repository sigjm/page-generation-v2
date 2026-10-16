import http.client
import io
import urllib.error

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from pagegen import llm


def save(tmp_path, im, name="p.png", **kw):
    file = tmp_path / name
    im.save(file, **kw)
    return file


def test_parse_json_strips_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('{"a": 1}') == {"a": 1}
    for bad in ["[1]", "", None, "not json"]:
        with pytest.raises(ValueError):
            llm.parse_json(bad)


def test_edit_input_pads_wide_photo_to_white_square(tmp_path):
    out = Image.open(io.BytesIO(llm.edit_input(save(tmp_path, Image.new("RGB", (400, 200), "red")))))
    assert out.size == (1024, 1024)
    assert all(c > 240 for c in out.getpixel((512, 10)))  # padding band is white
    r, g, b = out.getpixel((512, 512))
    assert r > 200 and g < 40  # product stays red, not stretched away


def test_transparent_png_flattens_to_white(tmp_path):
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (25, 25, 75, 75))
    out = Image.open(io.BytesIO(llm.model_jpeg(save(tmp_path, im))))
    assert all(c > 240 for c in out.getpixel((5, 5)))


def test_exif_rotation_applied(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # camera says: rotate 90° clockwise to display
    file = save(tmp_path, Image.new("RGB", (300, 100), "blue"), "r.jpg", exif=exif)
    assert Image.open(io.BytesIO(llm.model_jpeg(file))).size == (100, 300)
    assert Image.open(io.BytesIO(llm.edit_input(file))).getpixel((512, 512))[2] > 200


def test_chat_json_retries_once_then_raises(monkeypatch):
    replies = iter([{"content": "not json"}, {"content": '```json\n{"ok": true}\n```'}])
    monkeypatch.setattr(llm, "chat", lambda *a, **k: next(replies))
    assert llm.chat_json([], {}) == {"ok": True}
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": "nope"})
    with pytest.raises(llm.LLMError, match="두 번"):
        llm.chat_json([], {})


class FakeModel:
    """Stands in for ChatOpenAI: records how it was configured and returns one reply."""

    def __init__(self, reply, error=None):
        self.reply, self.error, self.bound, self.structured = reply, error, None, None

    def bind_tools(self, tools, **kw):
        self.bound = (tools, kw)
        return self

    def with_structured_output(self, schema, **kw):
        self.structured = (schema, kw)
        return self

    def invoke(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return {"raw": self.reply, "parsed": None, "parsing_error": None} if self.structured else self.reply


def use(monkeypatch, model):
    made = {}
    monkeypatch.setattr(llm, "chat_model", lambda **kw: made.update(kw) or model)
    return made


def test_chat_converts_tool_calls(monkeypatch):
    reply = AIMessage(content="", tool_calls=[{"name": "update_text", "args": {"section_id": "s1"}, "id": "c1"}])
    use(monkeypatch, FakeModel(reply))
    assert llm.chat([], tools=[{"type": "function"}])["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "update_text", "arguments": '{"section_id": "s1"}'}}]


def test_chat_keeps_invalid_tool_calls_raw(monkeypatch):
    reply = AIMessage(content="", invalid_tool_calls=[{"name": "update_text", "args": "{section_id: s1", "id": "c2", "error": "bad"}])
    use(monkeypatch, FakeModel(reply))
    assert llm.chat([], tools=[{"type": "function"}])["tool_calls"] == [
        {"id": "c2", "type": "function", "function": {"name": "update_text", "arguments": "{section_id: s1"}}]


@pytest.mark.parametrize("content, shown", [
    ("<think>음…</think>\n좋아요", "좋아요"),
    ("음… 추론…</think>\n\n좋아요", "좋아요"),  # template already opened <think>
    ("<think>사용자가 원하는 건", ""),  # thinking cut off by max_tokens
])
def test_chat_never_shows_reasoning(monkeypatch, content, shown):
    use(monkeypatch, FakeModel(AIMessage(content=content)))
    assert llm.chat([])["content"] == shown


def test_chat_configures_the_model(monkeypatch):
    model = FakeModel(AIMessage(content="{}"))
    made = use(monkeypatch, model)
    llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}], schema={"type": "object"},
             thinking=True, max_tokens=99, temperature=0.3, model="local/judge")
    assert made == {"thinking": True, "max_tokens": 99, "temperature": 0.3, "model": "local/judge"}
    assert model.bound[1] == {"tool_choice": "auto"}
    schema, kw = model.structured
    assert schema["type"] == "object" and schema["title"] == "out"
    assert kw == {"method": "json_schema", "strict": True, "include_raw": True}
    assert model.messages == [{"role": "user", "content": "x"}]


def test_chat_model_talks_to_mlx_serve():
    m = llm.chat_model(thinking=False, max_tokens=10, temperature=0.0)
    assert m.openai_api_base == f"{llm.MLX_URL}/v1" and m.model_name == llm.TEXT_MODEL and m.max_retries == 0
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


REQUEST = httpx.Request("POST", "http://127.0.0.1:11234/v1/chat/completions")


@pytest.mark.parametrize("error, message", [
    (openai.APIStatusError("bad", response=httpx.Response(400, text="PrefillDoesNotFit", request=REQUEST), body=None),
     "모델 서버 오류: 400 PrefillDoesNotFit"),
    (openai.APIConnectionError(request=REQUEST), "모델 서버 오류"),
    (openai.APITimeoutError(request=REQUEST), "모델 서버 오류"),
])
def test_api_errors_become_llm_error(monkeypatch, error, message):
    use(monkeypatch, FakeModel(None, error=error))
    with pytest.raises(llm.LLMError, match=message):
        llm.chat([])


@pytest.mark.parametrize("error", [http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"{")])
def test_image_server_dying_mid_response_becomes_llm_error(monkeypatch, error):
    def boom(*a, **k):
        raise error

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="모델 서버 오류"):
        llm._post("/v1/images/generations", {})


def test_image_http_error_keeps_the_body(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"PrefillDoesNotFit"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="400 PrefillDoesNotFit"):
        llm._post("/v1/images/generations", {})


def test_edit_image_request_shape(monkeypatch, tmp_path):
    sent = {}

    def fake_post(route, body, timeout=300):
        sent.update(body, route=route)
        return {"data": [{"b64_json": "UE5H"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm.edit_image(save(tmp_path, Image.new("RGB", (50, 50))), "대리석 위") == b"PNG"
    assert sent["route"] == "/v1/images/generations"
    assert {k: sent[k] for k in ("model", "mode", "size", "steps", "strength", "response_format")} == {
        "model": llm.IMAGE_MODEL, "mode": "edit", "size": "1024x1024", "steps": llm.IMAGE_STEPS,
        "strength": 0.3, "response_format": "b64_json"}
    assert sent["prompt"].startswith("대리석 위") and not sent["image"].startswith("data:")
