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
