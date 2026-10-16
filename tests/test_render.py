import pytest
from PIL import Image

from pagegen import render


def html_of(page):
    return render.render_html(page, lambda file: f"/files/{file}")


def test_sections_carry_ids_and_kinds_in_order(page):
    html = html_of(page)
    for section in page["sections"]:
        assert f'<section data-section-id="{section["id"]}" data-kind="{section["kind"]}"' in html
    assert html.index('"s1"') < html.index('"s2"') < html.index('"s3"')


def test_model_text_is_escaped(page):
    page["sections"][0]["headline"] = "<script>alert(1)</script>"
    page["product"]["name"] = "<b>이름</b>"
    html = html_of(page)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>" not in html


def test_points_and_spec_table_split_lines(page):
    page["sections"][1]["body"] = "넉넉한 용량: 500ml\n원터치:한 손으로"
    page["sections"].append({
        "id": "s4",
        "kind": "spec_table",
        "headline": "제품 정보",
        "body": "용량: 500ml\n운영 시간: 10:30",
    })
    html = html_of(page)
    assert "<strong>원터치</strong>" in html and "한 손으로" in html
    assert "<th>운영 시간</th><td>10:30</td>" in html


def test_photo_uses_photo_url_and_skips_unknown(page):
    assert 'src="/files/photos/p1-e1.png"' in html_of(page)
    page["sections"][0]["photo"] = "p9"
    assert "<img" not in html_of(page)


def test_no_external_resources_and_fixed_width(page):
    html = html_of(page)
    assert "http://" not in html and "https://" not in html and "@import" not in html
    assert "860px" in html


def test_export_png_jpg_and_sections(page, tmp_path):
    pytest.importorskip("playwright")
    photo = tmp_path / "p.png"
    Image.new("RGB", (860, 600), "orange").save(photo)
    html_file = tmp_path / "page.html"
    html_file.write_text(render.render_html(page, lambda file: photo.as_uri()), "utf-8")
    try:
        files = render.export(html_file.as_uri(), tmp_path / "export")
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("uv run playwright install chromium 필요")
        raise
    with Image.open(files["png"]) as image:
        assert image.width == 860 and image.height > 600
    assert files["jpg"].exists()
    assert [file.name for file in files["sections"]] == [
        "01-hero.png",
        "02-points.png",
        "03-notice.png",
    ]


def add(page, **section):
    page["sections"].append(section)
    return page


def test_variant_attribute_and_default(page):
    page["sections"][0]["variant"] = "nope"  # unknown → default
    html = html_of(add(page, id="s4", kind="scene", headline="장면", body="", photo="p1"))
    assert '<section data-section-id="s1" data-kind="hero" data-variant="below">' in html
    assert '<section data-section-id="s4" data-kind="scene">' in html  # kinds without variants get no attribute
    assert 'data-kind="points" data-variant="cards"' in html  # old page.json without variant still renders


def test_split_and_banner(page):
    add(page, id="s4", kind="split", variant="image-right", headline="한 손으로", body="누르면 열려요", photo="p1")
    add(page, id="s5", kind="banner", variant="dark", headline="<식은 커피>", body="")
    html = html_of(page)
    assert 'data-kind="split" data-variant="image-right"' in html and 'class="split-photo"' in html
    assert 'data-kind="banner" data-variant="dark"' in html and "&lt;식은 커피&gt;" in html


def test_gallery_renders_known_photos_only(page):
    html = html_of(add(page, id="s4", kind="gallery", variant="grid-3", headline="더 많은 모습", body="",
                       photos=["p1", "p9"]))
    gallery = html[html.index('data-kind="gallery"'):]
    assert gallery.count("<img") == 1 and 'data-count="1"' in gallery


def test_numbered_points_and_overlay_hero(page):
    page["sections"][0]["variant"] = "overlay"
    page["sections"][1]["variant"] = "numbered"
    html = html_of(page)
    assert 'data-variant="overlay"' in html and 'class="hero-copy"' in html
    assert 'data-kind="points" data-variant="numbered"' in html
