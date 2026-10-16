"""Render page.json to preview HTML and export it through Playwright (spec §8)."""
from collections.abc import Callable
from html import escape
from pathlib import Path
import shutil

from .page import KINDS, split_line

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:#1d1d1f;font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;word-break:keep-all}
main{width:860px;margin:0 auto}
section{padding:72px 64px;text-align:center}
section img{display:block;width:100%;height:auto}
h1{font-size:44px;line-height:1.3}
h2{font-size:32px;line-height:1.35;margin-bottom:28px}
section[data-kind=hero],section[data-kind=scene]{padding:0 0 72px}
section[data-kind=hero] h1{margin:56px 64px 16px}
section[data-kind=scene] h2{margin:48px 64px 12px}
section[data-kind=hero] p,section[data-kind=scene] p{font-size:20px;color:#555;line-height:1.6;margin:0 64px}
section[data-kind=points]{background:#f5f5f7}
section[data-kind=points] ul{list-style:none;display:grid;gap:16px;text-align:left}
section[data-kind=points] li{background:#fff;border-radius:16px;padding:28px 32px}
section[data-kind=points] strong{display:block;font-size:22px;margin-bottom:8px}
section[data-kind=points] span{font-size:18px;color:#555;line-height:1.6}
section[data-kind=spec_table] table{width:100%;border-collapse:collapse;text-align:left;font-size:18px}
section[data-kind=spec_table] th,section[data-kind=spec_table] td{padding:18px 8px;border-bottom:1px solid #e5e5ea}
section[data-kind=spec_table] th{width:30%;color:#6e6e73;font-weight:500}
section[data-kind=notice]{background:#f5f5f7;text-align:left}
section[data-kind=notice] p{font-size:17px;color:#555;line-height:1.7}
"""


def _lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.strip()]


def _section(section: dict, photos: dict[str, str], photo_url: Callable[[str], str]) -> str:
    kind = section["kind"] if section["kind"] in KINDS else "notice"
    headline = escape(section["headline"])
    body = section["body"]
    image = ""
    photo_id = section.get("photo")
    if kind in ("hero", "scene") and photo_id in photos:
        src = escape(photo_url(photos[photo_id]))
        image = f'<img src="{src}" alt="">'

    if kind == "hero":
        inner = f"{image}<h1>{headline}</h1><p>{escape(body)}</p>"
    elif kind == "scene":
        inner = f"{image}<h2>{headline}</h2><p>{escape(body)}</p>"
    elif kind == "points":
        items = "".join(
            f"<li><strong>{escape(key)}</strong><span>{escape(value)}</span></li>"
            for key, value in map(split_line, _lines(body))
        )
        inner = f"<h2>{headline}</h2><ul>{items}</ul>"
    elif kind == "spec_table":
        rows = "".join(
            f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>"
            for key, value in map(split_line, _lines(body))
        )
        inner = f"<h2>{headline}</h2><table>{rows}</table>"
    else:
        paragraphs = "".join(f"<p>{escape(line)}</p>" for line in _lines(body))
        inner = f"<h2>{headline}</h2>{paragraphs}"

    return f'<section data-section-id="{escape(section["id"])}" data-kind="{kind}">{inner}</section>'


def render_html(page: dict, photo_url: Callable[[str], str]) -> str:
    """Render the page with escaped model text and locally served photo URLs."""
    photos = {photo["id"]: photo["file"] for photo in page["photos"]}
    sections = "".join(_section(section, photos, photo_url) for section in page["sections"])
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<title>{escape(page["product"]["name"])}</title><style>{CSS}</style></head>'
        f"<body><main>{sections}</main></body></html>"
    )


def export(page_url: str, out_dir: Path) -> dict:
    """Export a full-page PNG/JPG and one PNG per section (spec §8)."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    shutil.rmtree(out_dir, ignore_errors=True)
    section_dir = out_dir / "sections"
    section_dir.mkdir(parents=True)
    png = out_dir / "page.png"
    jpg = out_dir / "page.jpg"
    sections = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 860, "height": 1200}, device_scale_factor=1)
            page.goto(page_url, wait_until="networkidle")
            page.screenshot(path=png, full_page=True)
            for index, element in enumerate(page.query_selector_all("section"), start=1):
                kind = element.get_attribute("data-kind")
                section_file = section_dir / f"{index:02d}-{kind}.png"
                element.screenshot(path=section_file)
                sections.append(section_file)
        finally:
            browser.close()

    with Image.open(png) as image:
        image.convert("RGB").save(jpg, quality=90)

    return {"png": png, "jpg": jpg, "sections": sections}
