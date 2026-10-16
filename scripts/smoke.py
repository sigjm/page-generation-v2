"""End-to-end check against a running server (spec §11).

    uv run python -m pagegen.server 2> server.log &
    uv run python scripts/smoke.py --photo product.jpg --out .orchestration/reports/2026-10-16-smoke

Makes three projects from one photo cropped square, landscape (4:3) and portrait (3:4): each runs the first
draft (with its staged scene edit). The square project then gets three chat edits and an export.
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

CHATS = [
    "헤드라인을 더 짧고 강하게 바꿔줘",
    "무게는 350g이에요. 스펙 표에 넣어줘",
    "대표 사진 배경을 밝은 대리석 테이블로 바꿔줘",
]


def crops(path: Path) -> dict[str, bytes]:
    im = Image.open(path).convert("RGB")
    w, h = im.size

    def center(rw: int, rh: int) -> bytes:
        scale = min(w / rw, h / rh)
        cw, ch = int(rw * scale), int(rh * scale)
        box = ((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2)
        buffer = io.BytesIO()
        im.crop(box).save(buffer, "JPEG", quality=92)
        return buffer.getvalue()

    return {"square": center(1, 1), "landscape": center(4, 3), "portrait": center(3, 4)}


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    response = fn(*args, **kwargs)
    return response, round(time.perf_counter() - started, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--name", default="스테인리스 텀블러")
    parser.add_argument("--notes", default="이중 진공 단열, 500ml, 색상 3종. 중성세제로 손세척 권장")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, default=Path(".orchestration/reports/smoke"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=args.server, timeout=600)
    report, failed = {}, False

    for label, data in crops(args.photo).items():
        r, seconds = timed(
            client.post,
            "/projects",
            data={"name": args.name, "notes": args.notes},
            files=[("photos", (f"{label}.jpg", data, "image/jpeg"))],
        )
        entry = {"status": r.status_code, "draft_seconds": seconds, "body": r.json()}
        report[label] = entry
        print(f"{label}: draft {r.status_code} in {seconds}s", flush=True)
        if r.status_code != 200:
            failed = True
            continue
        pid = entry["body"]["id"]
        page = client.get(f"/projects/{pid}").json()["page"]
        entry["sections"] = [(s["kind"], s["headline"], s["body"]) for s in page["sections"]]
        scene = next((s for s in page["sections"] if s["kind"] == "scene"), None)
        if scene:
            photo = next(p for p in page["photos"] if p["id"] == scene["photo"])
            (args.out / f"{label}-scene-{photo['id']}.png").write_bytes(
                client.get(f"/projects/{pid}/{photo['file']}").content
            )
        if label != "square":
            continue
        entry["chats"] = []
        for text in CHATS:
            r, seconds = timed(client.post, f"/projects/{pid}/chat", json={"text": text})
            body = r.json()
            entry["chats"].append({"text": text, "status": r.status_code, "seconds": seconds, "body": body})
            reply = body.get("reply", body)
            reply_text = reply.get("text", reply) if isinstance(reply, dict) else reply
            print(f"  chat {r.status_code} in {seconds}s: {text} -> {reply_text}", flush=True)
            failed |= r.status_code != 200
        final_page = client.get(f"/projects/{pid}").json()["page"]
        entry["final_sections"] = [(s["kind"], s["headline"], s["body"]) for s in final_page["sections"]]
        final_scene = next((s for s in final_page["sections"] if s["kind"] == "scene"), None)
        if final_scene:
            photo = next(p for p in final_page["photos"] if p["id"] == final_scene["photo"])
            (args.out / f"square-final-scene-{photo['id']}.png").write_bytes(
                client.get(f"/projects/{pid}/{photo['file']}").content
            )
        r, seconds = timed(client.post, f"/projects/{pid}/export")
        entry["export"] = {"status": r.status_code, "seconds": seconds, "body": r.json()}
        print(f"  export {r.status_code} in {seconds}s", flush=True)
        if r.status_code == 200:
            png = client.get(r.json()["png"]).content
            (args.out / "page.png").write_bytes(png)
            entry["export"]["png_size"] = Image.open(io.BytesIO(png)).size
        else:
            failed = True

    (args.out / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(f"report: {args.out / 'smoke.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
