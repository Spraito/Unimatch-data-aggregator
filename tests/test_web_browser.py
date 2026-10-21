"""Full browser workflows against the actual local Flask/SQLite application."""
import shutil
import threading
from pathlib import Path

import pytest

pytest.importorskip("flask")
api = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server
from webapp import create_app


@pytest.fixture
def live_site(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "browser.sqlite3")})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_explore_search_detail_save_compare_and_mobile(live_site):
    with api.sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
        if not Path(executable).exists():
            executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if not executable:
            pytest.skip("Install Chromium for end-to-end browser validation")
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors, external_requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: external_requests.append(request.url) if not request.url.startswith(live_site) else None)
            page.goto(live_site, wait_until="networkidle")
            api.expect(page.locator(".school-card")).to_have_count(4)
            api.expect(page.locator("#stat-programs")).to_have_text("55")

            page.locator("#search").fill("computer science")
            api.expect(page.locator(".school-card")).to_have_count(1)
            api.expect(page.locator(".school-card h3")).to_contain_text("Polytechnic")
            page.locator("#search").fill("no-such-school")
            api.expect(page.locator(".empty-state")).to_be_visible()
            page.get_by_role("button", name="Clear filters", exact=True).click()
            api.expect(page.locator(".school-card")).to_have_count(4)

            page.locator("#city").select_option("Quezon City")
            api.expect(page.locator(".school-card")).to_have_count(1)
            page.locator("#city").select_option("")
            api.expect(page.locator(".school-card")).to_have_count(4)
            pup = page.locator('[data-school-card="pup-sta-mesa"]')
            pup.get_by_role("button", name="Save PUP", exact=True).click()
            api.expect(page.locator("#saved-count")).to_have_text("1")
            page.reload(wait_until="networkidle")
            api.expect(page.locator("#saved-count")).to_have_text("1")

            page.locator('[data-school-card="pup-sta-mesa"] [data-action="details"]').click()
            api.expect(page.locator("#dialog-title")).to_contain_text("Polytechnic")
            page.get_by_role("tab", name="Admissions & fees").click()
            api.expect(page.locator("#detail-panel")).to_contain_text("2024-2025")
            api.expect(page.locator("#detail-panel")).to_contain_text("Sta. Mesa, Manila Application is closed")
            page.get_by_role("tab", name="Programs (55)").click()
            page.locator("#detail-program-search").fill("Computer Science")
            api.expect(page.locator(".detail-program-list li")).to_have_count(1)
            page.get_by_role("button", name="Close details").click()

            for school in ("pup-sta-mesa", "dlsu-manila", "ust-manila"):
                page.locator(f'[data-school-card="{school}"] [data-action="compare"]').click()
                api.expect(page.locator(f'[data-school-card="{school}"] [data-action="compare"]')).to_have_attribute("aria-pressed", "true")
            page.locator('[data-school-card="up-diliman"] [data-action="compare"]').click()
            api.expect(page.locator("#toast")).to_contain_text("three schools")
            page.locator('.nav-item[data-view="compare"]').click()
            api.expect(page.locator(".compare-table")).to_be_visible()
            api.expect(page.locator(".compare-table")).to_contain_text("2024-2025")
            assert page.locator(".compare-table thead h3").count() == 3

            page.locator('.nav-item[data-view="programs"]').click()
            api.expect(page.locator(".program-card")).to_have_count(12)
            page.locator("#topic").select_option("Computing")
            api.expect(page.locator(".program-card").first).to_be_visible()
            assert all("Computing" in text for text in page.locator(".program-card .tag").all_text_contents())
            page.locator("#topic").select_option("")
            page.get_by_role("button", name="Next", exact=True).click()
            api.expect(page.locator("#pagination")).to_contain_text("Page 2 of 5")

            page.locator('.nav-item[data-view="saved"]').click()
            api.expect(page.locator(".school-card")).to_have_count(1)
            page.get_by_role("button", name="Unsave PUP", exact=True).click()
            api.expect(page.locator(".empty-state")).to_be_visible()

            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Open navigation").click()
            page.locator('.nav-item[data-view="universities"]').click()
            api.expect(page.locator(".school-card")).to_have_count(4)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
            assert not errors
            assert not external_requests
        finally:
            browser.close()
