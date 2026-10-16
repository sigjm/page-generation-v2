from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


def test_hidden_forms_stay_hidden():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                pytest.skip("uv run playwright install chromium 필요")
            raise
        try:
            page = browser.new_page()
            page.goto(INDEX.as_uri())
            assert page.is_visible("#start") and not page.is_visible("#chat")
            page.evaluate("document.querySelector('#start').hidden = true; document.querySelector('#chat').hidden = false")
            assert not page.is_visible("#start") and page.is_visible("#chat")
        finally:
            browser.close()


def _photo(tmp_path, name, color):
    from PIL import Image

    file = tmp_path / name
    Image.new("RGB", (40, 30), color).save(file)
    return file


def test_photos_picked_one_at_a_time_are_all_uploaded(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            sent = {}

            def capture(route):
                sent["body"] = route.request.post_data_buffer
                route.fulfill(status=200, content_type="application/json",
                              body='{"id": "20261016-000000-abcd", "version": 1, "messages": []}')

            # fetch() is blocked on file:// pages, so serve index.html from a fake http origin
            page.route("http://pagegen.test/", lambda route: route.fulfill(status=200, content_type="text/html",
                                                                          body=INDEX.read_text("utf-8")))
            page.route("http://pagegen.test/projects", capture)
            page.route("http://pagegen.test/projects/*/page*", lambda route: route.fulfill(status=200, body="<html></html>"))
            page.goto("http://pagegen.test/")
            # some pickers (and every re-pick) hand over one file at a time: picks must add up, not replace
            page.set_input_files("#start input[type=file]", _photo(tmp_path, "front.png", "red"))
            page.set_input_files("#start input[type=file]", _photo(tmp_path, "side.png", "blue"))
            assert page.locator("#picked li").count() == 2
            page.locator("#picked li button").first.click()  # remove front.png
            page.set_input_files("#start input[type=file]", [_photo(tmp_path, "top.png", "green"),
                                                             _photo(tmp_path, "back.png", "gray")])
            page.fill("#start input[name=name]", "텀블러")
            page.click("#start button[type=submit]")
            page.wait_for_selector("#chat:not([hidden])")
            body = sent["body"]
            assert b"side.png" in body and b"top.png" in body and b"back.png" in body and b"front.png" not in body
        finally:
            browser.close()
