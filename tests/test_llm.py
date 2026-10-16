import http.client
import io
import urllib.error

import pytest
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


def test_http_error_becomes_llm_error_with_body(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"PrefillDoesNotFit"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="400 PrefillDoesNotFit"):
        llm.chat([{"role": "user", "content": "x"}])


def test_connection_refused_becomes_llm_error(monkeypatch):
    def refused(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refused)
    with pytest.raises(llm.LLMError, match="모델 서버 오류"):
        llm.chat([])


@pytest.mark.parametrize("content, shown", [
    ("<think>음…</think>\n좋아요", "좋아요"),
    ("음… 추론…</think>\n\n좋아요", "좋아요"),  # template already opened <think>
    ("<think>사용자가 원하는 건", ""),  # thinking cut off by max_tokens
])
def test_chat_never_shows_reasoning(monkeypatch, content, shown):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"choices": [{"message": {"content": content}}]})
    assert llm.chat([])["content"] == shown


@pytest.mark.parametrize("error", [http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"{")])
def test_server_dying_mid_response_becomes_llm_error(monkeypatch, error):
    def boom(*a, **k):
        raise error

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="모델 서버 오류"):
        llm.chat([])


def test_chat_sends_schema_tools_and_thinking(monkeypatch):
    sent = {}

    def fake_post(route, body, timeout=300):
        sent.update(body, route=route)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    llm.chat([], tools=[{"type": "function"}], schema={"type": "object"}, thinking=True)
    assert sent["route"] == "/v1/chat/completions"
    assert sent["model"] == llm.TEXT_MODEL
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent["tool_choice"] == "auto"
    assert sent["response_format"]["json_schema"]["schema"] == {"type": "object"}


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
