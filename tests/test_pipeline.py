"""All transport is mocked: tests never contact a university or registry."""
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from unimatch_ph import (
    Document, Extractor, FetchError, Page, Pipeline, PoliteFetcher,
    export, load_config, main, normalized_dates,
)

BASE = "https://example.test"
HTML = Path(__file__).with_name("fixture.html").read_bytes()
CONFIG = {
    "name": "Example University (synthetic fixture)",
    "campus_location": "Example campus",
    "pages": [{"url": BASE + "/admissions", "program_item_selector": ".program",
               "program_name_selector": ".name", "program_alignment_selector": ".alignment"}],
}


def response(status=200, body=b"", headers=None):
    result = Mock()
    result.status_code = status
    result.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
    result.iter_content.return_value = [body]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=None)
    return result


@pytest.fixture
def client(monkeypatch):
    fetcher = PoliteFetcher("UniMatchPHResearchBot/0.1 (+mailto:research@example.test)", .001, .001)
    monkeypatch.setattr("unimatch_ph.time.sleep", lambda _: None)
    yield fetcher
    fetcher.close()


def install_transport(monkeypatch, client, pages, robots=b"User-agent: *\nAllow: /\n"):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        assert "verify" not in kwargs  # requests' default verifies TLS
        if url.endswith("/robots.txt"):
            return response(body=robots, headers={"Content-Type": "text/plain"})
        return pages[url]()

    monkeypatch.setattr(client.session, "get", get)
    return calls


def test_end_to_end_json_and_csv(monkeypatch, client, tmp_path):
    calls = install_transport(monkeypatch, client, {BASE + "/admissions": lambda: response(body=HTML)})
    records = Pipeline(client).collect([CONFIG])
    record = records[0]
    assert record["collection_status"] == "collected"
    assert record["missing_fields"] == []
    assert len(record["programs"]) == 2
    assert record["programs"][0]["name"] == "BS Computer Science"
    assert record["programs"][0]["strand_alignment"].startswith("STEM")
    assert record["programs"][1]["strand_alignment"] is None
    fee = record["tuition_estimates"][0]
    assert fee["amounts"] == [{"min_php": 40000.0, "max_php": 60000.0}]
    assert fee["basis"] == "semester"
    assert record["application_deadlines"][0]["dates_iso"] == ["2027-03-15", "2027-03-20"]
    assert "Grade 12 report card" in record["admission_requirements"][0]["text"]
    assert "reasoning" in record["entrance_exam_coverage"][0]["text"]
    assert record["review_required"] is True
    export(records, tmp_path)
    assert json.loads((tmp_path / "unimatch_ph_raw_data.json").read_text()) == records
    frame = pd.read_csv(tmp_path / "unimatch_ph_raw_data.csv")
    assert len(frame) == 2
    assert json.loads(frame.iloc[0]["tuition_estimates"])[0]["amounts"] == fee["amounts"]
    assert calls == [BASE + "/robots.txt", BASE + "/admissions"]


def test_no_guessed_strand_or_costs():
    page = Page(BASE + "/programs")
    result = Extractor().extract(Document(page.url, "<h2>BS Computer Science</h2>"), page)
    assert result["programs"][0]["strand_alignment"] is None
    assert result["tuition_estimates"] == []


@pytest.mark.parametrize("text,expected", [
    ("March 15, 2027 and 20 Mar 2027", ["2027-03-15", "2027-03-20"]),
    ("2027-02-29 and March 35, 2027", []),
    ("03/04/2027; March 15; yesterday", []),
    ("2028-02-29; Sept. 1, 2027", ["2027-09-01", "2028-02-29"]),
])
def test_only_complete_unambiguous_valid_dates(text, expected):
    assert normalized_dates(text) == expected


@pytest.mark.parametrize("text,basis,amounts", [
    ("Tuition PHP 1,500 per unit", "unit", [{"min_php": 1500.0, "max_php": 1500.0}]),
    ("Tuition: free for eligible students", "unspecified_or_mixed", []),
    ("Tuition PHP 1,500 per unit or PHP 30,000 per semester", "unspecified_or_mixed",
     [{"min_php": 1500.0, "max_php": 1500.0}, {"min_php": 30000.0, "max_php": 30000.0}]),
])
def test_costs_do_not_invent_semester_totals(text, basis, amounts):
    item = Extractor._costs(text, BASE)
    assert item["basis"] == basis
    assert item["amounts"] == amounts
    assert item["text"] == text


def test_robots_disallow_wildcards(monkeypatch, client):
    calls = install_transport(monkeypatch, client, {}, b"User-agent: *\nDisallow: /private*\n")
    with pytest.raises(FetchError, match="disallows"):
        client.fetch(Page(BASE + "/private-admissions"))
    assert calls == [BASE + "/robots.txt"]


def test_redirect_checks_destination_robots(monkeypatch, client):
    calls = install_transport(monkeypatch, client, {
        BASE + "/admissions": lambda: response(302, headers={"Location": "/private"}),
    }, b"User-agent: *\nDisallow: /private\n")
    with pytest.raises(FetchError, match="disallows"):
        client.fetch(Page(BASE + "/admissions"))
    assert calls == [BASE + "/robots.txt", BASE + "/admissions"]


@pytest.mark.parametrize("status", [403, 429])
def test_stops_host_after_denial_without_retry(monkeypatch, client, status):
    calls = install_transport(monkeypatch, client, {BASE + "/admissions": lambda: response(status)})
    with pytest.raises(FetchError, match=str(status)):
        client.fetch(Page(BASE + "/admissions"))
    with pytest.raises(FetchError, match="stopped"):
        client.fetch(Page(BASE + "/other"))
    assert len(calls) == 2


@pytest.mark.parametrize("status,body", [(503, b"maintenance"), (200, b"<html>challenge</html>")])
def test_unknown_robots_fails_closed(monkeypatch, client, status, body):
    get = Mock(return_value=response(status, body))
    monkeypatch.setattr(client.session, "get", get)
    with pytest.raises(FetchError):
        client.fetch(Page(BASE + "/admissions"))
    assert get.call_count == 1


def test_404_robots_allows_public_page(monkeypatch, client):
    get = Mock(side_effect=[response(404), response(body=HTML)])
    monkeypatch.setattr(client.session, "get", get)
    assert client.fetch(Page(BASE + "/admissions")).html == HTML


def test_timeouts_preserve_partial_export(monkeypatch, client, tmp_path):
    def timeout():
        raise requests.Timeout("test timeout")
    install_transport(monkeypatch, client, {BASE + "/admissions": timeout})
    records = Pipeline(client).collect([CONFIG])
    assert records[0]["collection_status"] == "failed"
    assert records[0]["errors"]
    export(records, tmp_path)
    assert len(pd.read_csv(tmp_path / "unimatch_ph_raw_data.csv")) == 1


def test_delay_honors_crawl_delay(monkeypatch, client):
    monkeypatch.setattr("unimatch_ph.time.monotonic", lambda: 100)
    sleep = Mock()
    monkeypatch.setattr("unimatch_ph.time.sleep", sleep)
    client.last_request[BASE] = 99
    client._pause(BASE + "/admissions", crawl_delay=10)
    sleep.assert_called_once_with(9)


def test_cache_avoids_repeat_requests(monkeypatch, client):
    calls = install_transport(monkeypatch, client, {BASE + "/admissions": lambda: response(body=HTML)})
    page = Page(BASE + "/admissions")
    assert client.fetch(page) is client.fetch(page)
    assert len(calls) == 2


def test_invalid_selector_is_reported(monkeypatch, client):
    install_transport(monkeypatch, client, {BASE + "/admissions": lambda: response(body=HTML)})
    config = {**CONFIG, "pages": [{"url": BASE + "/admissions", "selectors": {"programs": "[broken"}}]}
    record = Pipeline(client).collect([config])[0]
    assert record["errors"]
    assert record["collection_status"] == "failed"


def test_cli_exports_and_signals_missing_fields(monkeypatch, tmp_path):
    config_path = tmp_path / "sources.json"
    config_path.write_text(json.dumps([CONFIG]))
    monkeypatch.setattr("unimatch_ph.time.sleep", lambda _: None)
    monkeypatch.setattr(requests.Session, "get", Mock(side_effect=[
        response(body=b"User-agent: *\nAllow: /\n"), response(body=b"<h1>No admissions data</h1>"),
    ]))
    monkeypatch.setattr("sys.argv", ["unimatch_ph.py", "--config", str(config_path),
                                    "--contact", "research@example.test", "--output-dir", str(tmp_path)])
    assert main() == 2
    data = json.loads((tmp_path / "unimatch_ph_raw_data.json").read_text())
    assert data[0]["collection_status"] == "partial"
    assert data[0]["missing_fields"]


def test_default_config_loads():
    config = load_config(Path(__file__).parents[1] / "config/universities.json")
    assert len(config) == 4


def test_browser_requires_explicit_enable(client):
    with pytest.raises(FetchError, match="--browser"):
        client.fetch(Page(BASE, render=True))


def test_line_separated_programs_preserve_inline_words_and_exclude_other_modes():
    config = load_config(Path(__file__).parents[1] / "config/universities.json")[1]
    page = Page(**config["pages"][1])
    html = '''<div class="page-content"><h2>Undergraduate Programs</h2>
      <p><strong>Open University System (OUS)</strong><br>Bachelor of Arts in Broadcasting</p>
      <p><strong>College of Computing</strong><br>Bachelor of Science in Computer Science<br>
        Bachelor of Science in Information <span>Technology</span></p>
      <p>Bachelor in Applied \n Statistics<br>Bachelor of \n Arts in History</p>
      <p><strong>Institute of Technology (ITECH)</strong><br>Diploma in Computing</p>
      </div><p>Bachelor of Arts in Outside Content</p>'''
    result = Extractor().extract(Document(page.url, html), page)
    assert [p["name"] for p in result["programs"]] == [
        "Bachelor of Science in Computer Science", "Bachelor of Science in Information Technology",
        "Bachelor in Applied Statistics", "Bachelor of Arts in History",
    ]


def test_pup_status_is_scoped_to_sta_mesa_and_preserves_year_context():
    university = load_config(Path(__file__).parents[1] / "config/universities.json")[1]
    page = Page(**university["pages"][0])
    html = '''<div class="page-content">
      <p>Online application for <strong>AY 2026-2027</strong></p>
      <div class="impt1"><p>Prepare a photo and report card.</p></div>
      <table id="scheds"><tr><td>Sta. Mesa, Manila</td><td>Application is closed</td></tr>
        <tr><td>Quezon City</td><td>Application is open</td></tr></table></div>'''
    result = Extractor().extract(Document(page.url, html), page)
    assert len(result["application_status"]) == 1
    status = result["application_status"][0]
    assert status["text"] == "Sta. Mesa, Manila Application is closed"
    assert status["source_context"] == ["Online application for AY 2026-2027"]
    assert "report card" in result["admission_requirements"][0]["text"]


def test_missing_content_selector_fails_visibly():
    with pytest.raises(FetchError, match="Content selector"):
        Extractor().extract(Document(BASE, "<p>Page has changed</p>"), Page(BASE, content_selector="main"))
