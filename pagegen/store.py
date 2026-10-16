"""Project folders on disk (spec §3): page.json, versions/, chat.json, photos/, export/."""
import io
import json
import os
import re
import secrets
import time
from pathlib import Path

from PIL import Image

ROOT = Path(os.environ.get("PROJECTS_DIR", "projects"))
MAX_PHOTOS = 6
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses to even open anything past 2x this
FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp"}  # MPO: camera/iPhone JPEG with extra frames
_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
_NAME = re.compile(r"[\w.-]+")
_NOT_A_PHOTO = "JPG·PNG·WebP 사진만 올릴 수 있습니다. HEIC는 JPG로 변환해 올려 주세요."


class NotFound(Exception):
    pass


class BadInput(Exception):
    """The message is shown to the user as-is."""


class NothingToUndo(Exception):
    pass


def path(pid: str) -> Path:
    if not _ID.fullmatch(pid):
        raise NotFound(pid)
    return ROOT / pid


def _extension(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise BadInput("사진은 한 장에 20MB까지 올릴 수 있습니다.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, pixels = im.format, im.width * im.height
            im.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise BadInput(_NOT_A_PHOTO) from e
    if fmt not in FORMATS:
        raise BadInput(_NOT_A_PHOTO)
    if pixels > MAX_PIXELS:
        raise BadInput("사진 해상도가 너무 큽니다(4,000만 화소까지).")
    return FORMATS[fmt]


def create(photos: list[bytes]) -> tuple[str, list[dict]]:
    """Validate every upload first, then save the original bytes untouched (spec §4.1-1)."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise BadInput(f"사진은 1~{MAX_PHOTOS}장 올려 주세요.")
    extensions = [_extension(data) for data in photos]
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    (ROOT / pid / "photos").mkdir(parents=True)
    records = []
    for i, (data, ext) in enumerate(zip(photos, extensions), 1):
        record = {"id": f"p{i}", "file": f"photos/p{i}.{ext}", "origin": "upload"}
        (ROOT / pid / record["file"]).write_bytes(data)
        records.append(record)
    return pid, records


def _read(file: Path):
    return json.loads(file.read_text("utf-8"))


def _write(file: Path, value) -> None:
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(file)


def load(pid: str) -> tuple[dict, list[dict]]:
    folder = path(pid)
    try:
        page = _read(folder / "page.json")
    except FileNotFoundError:
        raise NotFound(pid) from None
    chat = _read(folder / "chat.json") if (folder / "chat.json").exists() else []
    return page, chat


def save(pid: str, page: dict, chat: list[dict], snapshot: dict | None = None) -> None:
    """Write page.json and chat.json; `snapshot` (the page before this turn) goes to versions/ for undo."""
    folder = path(pid)
    if snapshot is not None:
        (folder / "versions").mkdir(exist_ok=True)
        _write(folder / "versions" / f"page.{snapshot['version']}.json", snapshot)
    _write(folder / "page.json", page)
    save_chat(pid, chat)


def save_chat(pid: str, chat: list[dict]) -> None:
    _write(path(pid) / "chat.json", chat)


def undo(pid: str) -> int:
    load(pid)  # unknown project → NotFound
    folder = path(pid)
    versions = sorted((folder / "versions").glob("page.*.json"), key=lambda f: int(f.name.split(".")[1]))
    if not versions:
        raise NothingToUndo(pid)
    page = _read(versions[-1])
    _write(folder / "page.json", page)
    versions[-1].unlink()
    return page["version"]


def served_file(pid: str, rel: str) -> Path:
    """A file the browser may fetch: a photo listed in page.json, or a file under export/ (spec §7)."""
    page, _ = load(pid)
    folder = path(pid)
    parts = rel.split("/")
    if not all(_NAME.fullmatch(part) and part not in (".", "..") for part in parts):
        raise NotFound(rel)
    file = (folder / rel).resolve()
    listed = rel in {p["file"] for p in page["photos"]}
    exported = parts[0] == "export" and file.is_relative_to((folder / "export").resolve())
    if not (listed or exported) or not file.is_file():
        raise NotFound(rel)
    return file
