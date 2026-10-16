import io
import json

import pytest
from PIL import Image

from pagegen import store


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    return tmp_path


def image_bytes(fmt="JPEG", size=(40, 30), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, "red").save(buffer, fmt)
    return buffer.getvalue()


def make_project():
    pid, photos = store.create([image_bytes()])
    page = {"id": pid, "version": 1, "photos": photos, "sections": []}
    store.save(pid, page, [])
    return pid, page


def test_create_saves_original_bytes_with_detected_extension(root):
    jpg, png = image_bytes("JPEG"), image_bytes("PNG", mode="RGBA")
    pid, photos = store.create([jpg, png])
    assert photos == [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
                      {"id": "p2", "file": "photos/p2.png", "origin": "upload"}]
    assert (root / pid / "photos/p1.jpg").read_bytes() == jpg


@pytest.mark.parametrize("payload, message", [
    ([b"not an image"], "JPG·PNG·WebP"),
    ([image_bytes("GIF")], "JPG·PNG·WebP"),
    ([], "1~6장"),
    ([image_bytes()] * 7, "1~6장"),
])
def test_create_rejects_bad_uploads_before_writing(root, payload, message):
    with pytest.raises(store.BadInput, match=message):
        store.create(payload)
    assert list(root.iterdir()) == []


def test_create_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(store, "MAX_BYTES", 10)
    with pytest.raises(store.BadInput, match="20MB"):
        store.create([image_bytes()])


def test_create_rejects_too_many_pixels(monkeypatch):
    monkeypatch.setattr(store, "MAX_PIXELS", 100)
    with pytest.raises(store.BadInput, match="해상도"):
        store.create([image_bytes(size=(20, 20))])


@pytest.mark.parametrize("pid", ["../etc", "20261016-153012-ab1", "x", "20261016-153012-ab12/.."])
def test_path_rejects_bad_ids(pid):
    with pytest.raises(store.NotFound):
        store.path(pid)


def test_load_missing_project():
    with pytest.raises(store.NotFound):
        store.load("20261016-153012-ab12")


def test_save_snapshot_and_undo():
    pid, page = make_project()
    before = json.loads(json.dumps(page))
    page["version"] = 2
    page["sections"].append({"id": "s1"})
    store.save(pid, page, [{"role": "user", "text": "hi"}], snapshot=before)
    assert store.undo(pid) == 1
    restored, chat = store.load(pid)
    assert restored["sections"] == [] and chat == [{"role": "user", "text": "hi"}]
    with pytest.raises(store.NothingToUndo):
        store.undo(pid)


def test_served_file_only_listed_photos_and_export(root):
    pid, _ = make_project()
    assert store.served_file(pid, "photos/p1.jpg").name == "p1.jpg"
    (root / pid / "photos/secret.jpg").write_bytes(b"x")
    (root / pid / "export/sections").mkdir(parents=True)
    (root / pid / "export/sections/01-hero.png").write_bytes(b"x")
    assert store.served_file(pid, "export/sections/01-hero.png").name == "01-hero.png"
    for rel in ["photos/secret.jpg", "page.json", "export/../page.json", "photos/../page.json", "export/nope.png", "export"]:
        with pytest.raises(store.NotFound):
            store.served_file(pid, rel)


def test_create_accepts_multi_picture_jpeg(root):
    # Camera JPEGs with an embedded preview (and iPhone HDR exports) open as Pillow format "MPO"
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, "MPO", save_all=True, append_images=[Image.new("RGB", (20, 15), "blue")])
    _, photos = store.create([buffer.getvalue()])
    assert photos[0]["file"] == "photos/p1.jpg"
