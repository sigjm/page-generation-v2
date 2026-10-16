import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pagegen import agent, server, store
from pagegen.llm import LLMError


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch, page):
    monkeypatch.setattr(store, "ROOT", tmp_path)

    def fake_draft(folder, pid, product, photos, llm=None):
        return dict(page, id=pid, product=product, photos=photos), {"role": "assistant", "text": "초안", "tools": []}

    monkeypatch.setattr(agent, "make_draft", fake_draft)
    return TestClient(server.app)


def create(client):
    r = client.post("/projects", data={"name": "텀블러", "notes": "500ml"},
                    files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["messages"][0]["text"] == "초안"
    return r.json()["id"]


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "상세페이지 에이전트" in r.text


def test_create_get_preview_and_photo(client):
    pid = create(client)
    data = client.get(f"/projects/{pid}").json()
    assert data["page"]["product"] == {"name": "텀블러", "notes": "500ml"}
    html = client.get(f"/projects/{pid}/page")
    assert html.status_code == 200 and 'data-section-id="s1"' in html.text
    assert html.headers["cache-control"] == "no-store"
    assert client.get(f"/projects/{pid}/photos/p1.jpg").status_code == 200


@pytest.mark.parametrize("data, files", [
    ({"name": " "}, [("photos", ("a.jpg", jpeg(), "image/jpeg"))]),
    ({"name": "x"}, [("photos", ("a.txt", b"hello", "text/plain"))]),
    ({"name": "x"}, None),
])
def test_create_rejects_bad_input_with_400(client, data, files):
    r = client.post("/projects", data=data, files=files)
    assert r.status_code == 400 and r.json()["error"]


def test_chat_change_bumps_version_then_undo(client, monkeypatch):
    pid = create(client)

    def fake_turn(folder, page, chat, text, llm=None):
        chat.append({"role": "user", "text": text})
        page["sections"].pop()
        reply = {"role": "assistant", "text": "지웠어요", "tools": []}
        chat.append(reply)
        return reply, True

    monkeypatch.setattr(agent, "chat_turn", fake_turn)
    r = client.post(f"/projects/{pid}/chat", json={"text": "마지막 섹션 지워"})
    assert r.json() == {"reply": {"role": "assistant", "text": "지웠어요", "tools": []}, "version": 2}
    assert client.post(f"/projects/{pid}/undo").json() == {"version": 1}
    data = client.get(f"/projects/{pid}").json()
    assert len(data["page"]["sections"]) == 3 and len(data["chat"]) == 3  # undo keeps the conversation
    assert client.post(f"/projects/{pid}/undo").status_code == 409


def test_chat_without_change_keeps_version(client, monkeypatch):
    pid = create(client)

    def fake_turn(folder, page, chat, text, llm=None):
        chat += [{"role": "user", "text": text}, {"role": "assistant", "text": "네", "tools": []}]
        return chat[-1], False

    monkeypatch.setattr(agent, "chat_turn", fake_turn)
    assert client.post(f"/projects/{pid}/chat", json={"text": "고마워"}).json()["version"] == 1
    assert client.post(f"/projects/{pid}/undo").status_code == 409


def test_chat_model_error_keeps_page_and_user_message(client, monkeypatch):
    pid = create(client)

    def down(folder, page, chat, text, llm=None):
        chat.append({"role": "user", "text": text})
        page["sections"].clear()
        raise LLMError("모델 서버 오류: 연결 거부")

    monkeypatch.setattr(agent, "chat_turn", down)
    r = client.post(f"/projects/{pid}/chat", json={"text": "안녕"})
    assert r.status_code == 502 and "연결 거부" in r.json()["error"]
    data = client.get(f"/projects/{pid}").json()
    assert len(data["page"]["sections"]) == 3 and data["chat"][-1] == {"role": "user", "text": "안녕"}


def test_draft_model_error_is_502(client, monkeypatch):
    def down(*a, **k):
        raise LLMError("모델 서버 오류: 연결 거부")

    monkeypatch.setattr(agent, "make_draft", down)
    r = client.post("/projects", data={"name": "x"}, files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 502 and "연결 거부" in r.json()["error"]


def test_empty_chat_message_is_400(client):
    pid = create(client)
    assert client.post(f"/projects/{pid}/chat", json={"text": "  "}).status_code == 400


def test_unknown_project_and_unlisted_files_404(client):
    assert client.get("/projects/20261016-153012-ab12").status_code == 404
    assert client.get("/projects/not-an-id/page").status_code == 404
    pid = create(client)
    assert client.get(f"/projects/{pid}/photos/p9.jpg").status_code == 404
    assert client.get(f"/projects/{pid}/export/page.png").status_code == 404


def test_export_returns_project_urls(client, monkeypatch):
    pid = create(client)

    def fake_export(page_url, out_dir):
        assert page_url.endswith(f"/projects/{pid}/page")
        (out_dir / "sections").mkdir(parents=True)
        files = {"png": out_dir / "page.png", "jpg": out_dir / "page.jpg", "sections": [out_dir / "sections/01-hero.png"]}
        for f in [files["png"], files["jpg"], *files["sections"]]:
            f.write_bytes(b"x")
        return files

    monkeypatch.setattr(server.render, "export", fake_export)
    data = client.post(f"/projects/{pid}/export").json()
    assert data == {"png": f"/projects/{pid}/export/page.png", "jpg": f"/projects/{pid}/export/page.jpg",
                    "sections": [f"/projects/{pid}/export/sections/01-hero.png"]}
    assert client.get(data["sections"][0]).status_code == 200
