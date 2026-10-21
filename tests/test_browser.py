"""Exercise actual JavaScript rendering with a synthetic, offline transport.

No certificate checks are disabled: there are no outgoing browser HTTP calls.
Only Route.fetch's transport is replaced with deterministic fixture responses.
"""
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from unimatch_ph import Extractor, Page
from test_pipeline import BASE, install_transport

# Import the shared pytest fixture into this module's fixture namespace.
from test_pipeline import client  # noqa: F401


def test_javascript_program_extraction(monkeypatch, client):
    api = pytest.importorskip("playwright.sync_api")
    bundled = None
    with api.sync_playwright() as playwright:
        if Path(playwright.chromium.executable_path).exists():
            bundled = playwright.chromium.executable_path
    executable = bundled or shutil.which("chromium") or shutil.which("chromium-browser")
    if not executable:
        pytest.skip("Install Playwright Chromium to test rendering")
    client.browser = True
    client.browser_executable = executable
    install_transport(monkeypatch, client, {})
    requested = []
    original_fulfill = api.Route.fulfill
    resources = {
        BASE + "/dynamic": ("text/html", '<html><div id="programs"></div><script src="/script.js"></script></html>'),
        BASE + "/script.js": ("application/javascript", '''
            fetch('/data.json').then(r => r.json()).then(data => {
                const h = document.createElement('h2');
                h.textContent = data.name;
                document.querySelector('#programs').append(h);
            });
        '''),
        BASE + "/data.json": ("application/json", '{"name": "BS Computer Science"}'),
    }

    def fixture_fetch(route, **kwargs):
        assert kwargs["max_redirects"] == 0
        requested.append(route.request.url)
        content_type, body = resources[route.request.url]
        return SimpleNamespace(status=200, headers={"Content-Type": content_type}, fixture_body=body)

    def fixture_fulfill(route, *, response):
        original_fulfill(route, status=response.status, headers=response.headers, body=response.fixture_body)

    monkeypatch.setattr(api.Route, "fetch", fixture_fetch)
    monkeypatch.setattr(api.Route, "fulfill", fixture_fulfill)
    config = Page(BASE + "/dynamic", fields=["programs"], render=True, wait_selector="#programs h2")
    document = client.fetch(config)
    programs = Extractor().extract(document, config)["programs"]
    assert programs[0]["name"] == "BS Computer Science"
    assert requested == [BASE + "/dynamic", BASE + "/script.js", BASE + "/data.json"]
