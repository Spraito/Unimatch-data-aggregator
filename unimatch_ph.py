"""Conservative extraction from explicitly configured, public university pages.

This is a raw-data collection tool, not an authoritative admissions database.
Every extracted fact includes its source; all results require human review.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from protego import Protego
from soupsieve.util import SelectorSyntaxError

LOG = logging.getLogger("unimatch")
FIELDS = (
    "programs", "tuition_estimates", "admission_requirements",
    "entrance_exam_coverage", "application_deadlines", "application_status",
)
# Heuristics find candidate sections, never invent facts missing from a source.
HINTS = {
    "tuition_estimates": r"tuition|school fees|estimated cost|cost per semester",
    "admission_requirements": r"admission requirements|application requirements|eligibility|required documents",
    "entrance_exam_coverage": r"exam(?:ination)? coverage|test coverage|test components|subtests",
    "application_deadlines": r"deadline|application (?:period|schedule)|important dates",
    "application_status": r"applications? (?:are |is )?(?:now )?(?:open|closed)|application status",
}
PROGRAM = re.compile(
    r"^(?:Bachelor\s+(?:of|in)\s+|B\.?\s?S\.?\s+|B\.?\s?A\.?\s+|A\.?\s?B\.?\s+|"
    r"BSc\s+|BSEd\s+|BEEd\s+)", re.I,
)
MONTHS = {name: i for i, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1
)}
MONTH_TOKEN = "(?:" + "|".join(sorted(
    set(MONTHS) | {m[:3] for m in MONTHS} | {"sept"}, key=len, reverse=True
)) + r")\.?"
CURRENCY = r"(?:PHP\.?|₱|(?<!\w)P\.?)\s*"
NUMBER = r"\d+(?:,\d{3})*(?:\.\d{1,2})?"
MONEY = re.compile(
    rf"{CURRENCY}(?P<low>{NUMBER})(?:\s*(?:[-–—]|to)\s*(?:{CURRENCY})?(?P<high>{NUMBER}))?", re.I,
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def https_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError(f"Expected a public HTTPS URL without credentials: {value!r}")
    if parts.port not in (None, 443):
        raise ValueError("Only the standard HTTPS port is supported")
    return value


def origin(url: str) -> str:
    parts = urlsplit(https_url(url))
    return f"https://{parts.netloc.lower()}"


def normalized_dates(text: str) -> list[str]:
    """Only complete, explicit dates; ambiguous numeric dates remain in raw text."""
    dates: set[str] = set()

    def add(year: str, month: int, day: str) -> None:
        try:
            dates.add(datetime(int(year), month, int(day)).date().isoformat())
        except ValueError:
            pass

    for y, m, d in re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        add(y, int(m), d)
    for m, d, y in re.findall(
        rf"\b({MONTH_TOKEN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(20\d{{2}})\b", text, re.I
    ):
        month = next(v for k, v in MONTHS.items() if k.startswith(m.lower().rstrip(".")))
        add(y, month, d)
    for d, m, y in re.findall(
        rf"\b(\d{{1,2}})\s+({MONTH_TOKEN})\s+(20\d{{2}})\b", text, re.I
    ):
        month = next(v for k, v in MONTHS.items() if k.startswith(m.lower().rstrip(".")))
        add(y, month, d)
    return sorted(dates)


class FetchError(RuntimeError):
    """A source could not be collected safely or successfully."""


@dataclass
class Page:
    url: str
    fields: list[str] = field(default_factory=lambda: list(FIELDS))
    selectors: dict[str, str] = field(default_factory=dict)
    render: bool = False
    wait_selector: str | None = None
    allowed_render_hosts: list[str] = field(default_factory=list)
    # A card selector plus relative selectors permits explicit strand evidence.
    program_item_selector: str | None = None
    program_name_selector: str | None = None
    program_alignment_selector: str | None = None
    program_line_selector: str | None = None
    content_selector: str | None = None
    context_selector: str | None = None

    def __post_init__(self) -> None:
        https_url(self.url)
        if not isinstance(self.fields, list) or not all(isinstance(f, str) for f in self.fields):
            raise ValueError("Page fields must be a list of field names")
        if not isinstance(self.selectors, dict) or not all(isinstance(s, str) for s in self.selectors.values()):
            raise ValueError("Page selectors must map field names to CSS selector strings")
        if not isinstance(self.render, bool):
            raise ValueError("Page render must be a JSON boolean")
        unknown = (set(self.fields) | set(self.selectors)) - set(FIELDS)
        if unknown:
            raise ValueError(f"Unknown extraction fields: {sorted(unknown)}")


@dataclass
class Document:
    url: str
    html: str | bytes


class PoliteFetcher:
    """One sequential client; robots and host delays apply to redirects, too."""

    def __init__(self, user_agent: str, min_delay: float = 2, max_delay: float = 5,
                 timeout: float = 25, browser: bool = False, browser_executable: str | None = None):
        if (not all(math.isfinite(n) for n in (min_delay, max_delay, timeout))
                or not 0 < min_delay <= max_delay or timeout <= 0):
            raise ValueError("Require 0 < min-delay <= max-delay and timeout > 0")
        self.user_agent = user_agent
        self.min_delay, self.max_delay, self.timeout = min_delay, max_delay, timeout
        self.browser = browser
        self.browser_executable = browser_executable
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html, text/plain;q=0.9"})
        self.robots: dict[str, Protego] = {}
        self.last_request: dict[str, float] = {}
        self.blocked: set[str] = set()
        self.cache: dict[str, Document] = {}

    def close(self) -> None:
        self.session.close()

    def _pause(self, url: str, crawl_delay: float = 0) -> None:
        host = origin(url)
        delay = max(random.uniform(self.min_delay, self.max_delay), crawl_delay)
        remaining = self.last_request.get(host, 0) + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[host] = time.monotonic()

    def _get(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        """Bounded downloads, verified TLS, no automatic retries or redirects."""
        self._pause(url, self.robots.get(origin(url), Protego.parse("")).crawl_delay(self.user_agent) or 0)
        try:
            with self.session.get(url, timeout=self.timeout, allow_redirects=False, stream=True) as response:
                if response.status_code in (403, 429):
                    self.blocked.add(origin(url))
                    raise FetchError(f"HTTP {response.status_code}; stopped requests to {origin(url)}")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise FetchError("Response exceeds 4 MiB limit")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks), dict(response.headers)
        except requests.RequestException as exc:
            raise FetchError(f"Network/TLS error: {exc}") from exc

    def _policy(self, url: str) -> Protego:
        host = origin(url)
        if host in self.blocked:
            raise FetchError(f"Requests to {host} stopped after an access/rate-limit failure")
        if host not in self.robots:
            robots_url = host + "/robots.txt"
            # Only same-origin robots redirects; never silently assume permission.
            for _ in range(6):
                status, body, headers = self._get(robots_url)
                if status in (301, 302, 303, 307, 308):
                    target = urljoin(robots_url, headers.get("Location", ""))
                    if origin(target) != host:
                        raise FetchError("Cross-origin robots redirect requires manual review")
                    robots_url = target
                    continue
                if status in (404, 410):
                    policy = Protego.parse("")
                elif 200 <= status < 300:
                    text = body.decode("utf-8-sig", errors="replace")
                    if re.search(r"<\s*(?:!doctype|html)\b", text, re.I):
                        raise FetchError("robots.txt returned HTML; policy cannot be established")
                    policy = Protego.parse(text)
                else:
                    raise FetchError(f"Cannot establish robots policy: HTTP {status}")
                self.robots[host] = policy
                break
            else:
                raise FetchError("Too many robots.txt redirects")
        return self.robots[host]

    def permitted(self, url: str) -> Protego:
        policy = self._policy(url)
        if not policy.can_fetch(url, self.user_agent):
            raise FetchError(f"robots.txt disallows {url}")
        return policy

    @staticmethod
    def _check_html(body: str | bytes) -> None:
        soup = BeautifulSoup(body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if re.search(r"just a moment|access denied|verify you are human|attention required|captcha", title, re.I):
            raise FetchError("Access challenge detected; manual access/permission required")

    def fetch(self, page: Page) -> Document:
        key = ("browser:" if page.render else "static:") + page.url
        if key in self.cache:
            return self.cache[key]
        if page.render:
            if not self.browser:
                raise FetchError("Page requires rendering; install Playwright and pass --browser")
            result = self._render(page)
        else:
            url = page.url
            for _ in range(6):
                self.permitted(url)
                status, body, headers = self._get(url)
                if status in (301, 302, 303, 307, 308):
                    url = urljoin(url, headers.get("Location", ""))
                    https_url(url)
                    continue
                if not 200 <= status < 300:
                    raise FetchError(f"HTTP {status}: {url}")
                content_type = headers.get("Content-Type", "").lower()
                if content_type and "html" not in content_type:
                    raise FetchError(f"Expected HTML, received {content_type}")
                self._check_html(body)
                result = Document(url, body)
                break
            else:
                raise FetchError("Too many page redirects")
        self.cache[key] = result
        return result

    def _render(self, page_config: Page) -> Document:
        """Render opt-in pages; no login, forms, CAPTCHA bypass, or stealth mode.

        Route every browser request through robots checks and host delays. Images,
        fonts, media, non-GET requests, and unapproved external hosts are blocked.
        Service workers are disabled so they cannot bypass request routing.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError("Install requirements-browser.txt and Chromium first") from exc
        self.permitted(page_config.url)
        allowed = {urlsplit(page_config.url).hostname, *page_config.allowed_render_hosts}
        failures: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=self.browser_executable)
            try:
                context = browser.new_context(user_agent=self.user_agent, service_workers="block", accept_downloads=False)

                def route_request(route: Any) -> None:
                    request = route.request
                    if (urlsplit(request.url).hostname not in allowed
                            or request.method not in ("GET", "HEAD")
                            or request.resource_type in ("image", "media", "font")):
                        route.abort()
                        return
                    try:
                        policy = self.permitted(request.url)
                        self._pause(request.url, policy.crawl_delay(self.user_agent) or 0)
                        response = route.fetch(max_redirects=0, timeout=self.timeout * 1000)
                        if response.status in (403, 429):
                            self.blocked.add(origin(request.url))
                            raise FetchError(f"Browser HTTP {response.status}: {request.url}")
                        route.fulfill(response=response)
                    except Exception as exc:
                        failures.append(str(exc))
                        route.abort()

                context.route("**/*", route_request)
                tab = context.new_page()
                response = tab.goto(page_config.url, wait_until="networkidle", timeout=self.timeout * 1000)
                if page_config.wait_selector:
                    tab.wait_for_selector(page_config.wait_selector, timeout=self.timeout * 1000)
                if not response or not response.ok or failures:
                    raise FetchError(f"Rendering incomplete: {failures or 'document request failed'}")
                html = tab.content()
                self._check_html(html)
                return Document(tab.url, html)
            finally:
                browser.close()


class Extractor:
    """Selectors are preferred; heading/label heuristics yield review candidates."""

    @staticmethod
    def _blocks(soup: BeautifulSoup, field_name: str, config: Page) -> list[str]:
        if field_name in config.selectors:
            return list(dict.fromkeys(
                text for node in soup.select(config.selectors[field_name])
                if (text := clean(node.get_text(" ", strip=True)))
            ))
        pattern = re.compile(HINTS[field_name], re.I)
        found = []
        for heading in soup.find_all(re.compile(r"^h[1-6]$")):
            if not pattern.search(heading.get_text(" ", strip=True)):
                continue
            parts = [heading.get_text(" ", strip=True)]
            for following in heading.next_elements:
                if isinstance(following, Tag) and re.fullmatch(r"h[1-6]", following.name):
                    if int(following.name[1]) <= int(heading.name[1]):
                        break
                if isinstance(following, NavigableString) and heading not in following.parents:
                    parts.append(str(following))
            text = clean(" ".join(parts))
            if text:
                found.append(text)
        for node in soup.select("p, li, tr"):
            text = clean(node.get_text(" ", strip=True))
            if len(text) <= 1500 and pattern.search(text):
                found.append(text)
        # Deduplicate but retain separate source statements; do not truncate evidence.
        return list(dict.fromkeys(found))

    @staticmethod
    def _programs(soup: BeautifulSoup, config: Page, url: str) -> list[dict[str, Any]]:
        if config.program_line_selector:
            # Some official lists separate programs using <br>, with inline
            # spans and physical whitespace inside individual degree names.
            # Split only at actual <br> elements, not HTML source newlines.
            programs = []
            for block in soup.select(config.program_line_selector):
                copy = BeautifulSoup(str(block), "html.parser")
                for br in copy.find_all("br"):
                    br.replace_with("\ue000")
                for line in copy.get_text(" ", strip=True).split("\ue000"):
                    name = clean(line)
                    if PROGRAM.match(name) and len(name) <= 180:
                        programs.append({"name": name, "strand_alignment": None,
                                         "source_url": url, "extraction_method": "selector_lines"})
            return programs
        explicit = config.program_item_selector or config.selectors.get("programs")
        nodes = soup.select(explicit or "a, li, td, h1, h2, h3, h4")
        programs = []
        for node in nodes:
            name_node = node.select_one(config.program_name_selector) if config.program_name_selector else node
            if name_node is None:
                continue
            name = clean(name_node.get_text(" ", strip=True))
            if not name or len(name) > 180 or (not explicit and not PROGRAM.match(name)):
                continue
            alignment = node.select_one(config.program_alignment_selector) if config.program_alignment_selector else None
            programs.append({
                "name": name,
                # Eligibility/strand alignment must be explicitly stated, never inferred from the degree name.
                "strand_alignment": clean(alignment.get_text(" ", strip=True)) if alignment else None,
                "source_url": url,
                "extraction_method": "selector" if explicit else "heuristic",
            })
        return programs

    @staticmethod
    def _costs(text: str, url: str) -> dict[str, Any]:
        amounts = []
        for match in MONEY.finditer(text):
            low = float(match["low"].replace(",", ""))
            high = float(match["high"].replace(",", "")) if match["high"] else low
            amounts.append({"min_php": low, "max_php": high} if high >= low else {"raw": match[0]})
        # Multiple fees or time bases cannot safely become one semester estimate.
        bases = [name for name, regex in {
            "semester": r"per semester|/\s*semester|each semester",
            "trimester": r"per trimester|/\s*trimester",
            "unit": r"per unit|/\s*unit",
            "year": r"per (?:academic )?year|annual|/\s*year",
        }.items() if re.search(regex, text, re.I)]
        return {"text": text, "amounts": amounts, "currency": "PHP" if amounts else None,
                "basis": bases[0] if len(bases) == 1 else "unspecified_or_mixed",
                "scope": "source page; program applicability requires review", "source_url": url}

    def extract(self, document: Document, config: Page) -> dict[str, list]:
        soup = BeautifulSoup(document.html, "html.parser")
        for node in soup.select("script, style, nav, footer, noscript, template"):
            node.decompose()
        context = [clean(node.get_text(" ", strip=True)) for node in soup.select(config.context_selector)] if config.context_selector else []
        if config.content_selector:
            # Fail visibly if the configured content area disappears.
            roots = soup.select(config.content_selector)
            if not roots:
                raise FetchError(f"Content selector matched no elements: {config.content_selector}")
            soup = BeautifulSoup("\n".join(str(root) for root in roots), "html.parser")
        result: dict[str, list] = {name: [] for name in FIELDS}
        for name in config.fields:
            if name == "programs":
                result[name] = self._programs(soup, config, document.url)
                continue
            for text in self._blocks(soup, name, config):
                method = "selector" if name in config.selectors else "heuristic"
                item = {"text": text, "source_url": document.url, "extraction_method": method}
                if name == "tuition_estimates":
                    item.update(self._costs(text, document.url))
                elif name == "application_deadlines":
                    item["dates_iso"] = normalized_dates(text)
                    item["interpretation"] = "source dates; deadline type, intake and applicability require review"
                result[name].append(item)
        if context:
            for items in result.values():
                for item in items:
                    item["source_context"] = context
        return result


class Pipeline:
    def __init__(self, fetcher: PoliteFetcher):
        self.fetcher = fetcher
        self.extractor = Extractor()

    def collect(self, universities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for university in universities:
            record = {
                "university_name": university["name"],
                "campus_location": university["campus_location"],
                "identity_source": "configuration; verify campus applicability of each source",
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "review_required": True,
                "source_notes": university.get("source_notes", []),
                **{name: [] for name in FIELDS},
                "sources": [], "errors": [],
            }
            for raw_page in university["pages"]:
                config = Page(**raw_page)
                LOG.info("Collecting %s", config.url)
                try:
                    document = self.fetcher.fetch(config)
                    extracted = self.extractor.extract(document, config)
                    for name, items in extracted.items():
                        record[name].extend(item for item in items if item not in record[name])
                    record["sources"].append({"requested_url": config.url, "final_url": document.url,
                                              "status": "fetched", "fields": config.fields})
                except (FetchError, requests.RequestException, SelectorSyntaxError) as exc:
                    message = str(exc)
                    LOG.warning("Skipping %s: %s", config.url, message)
                    record["errors"].append({"url": config.url, "message": message})
                    record["sources"].append({"requested_url": config.url, "status": "failed"})
                except Exception as exc:
                    # Playwright errors are optional imports. Preserve partial results,
                    # flag the page failure, and log a traceback for diagnosis.
                    LOG.exception("Unexpected failure for %s", config.url)
                    record["errors"].append({"url": config.url, "message": f"{type(exc).__name__}: {exc}"})
                    record["sources"].append({"requested_url": config.url, "status": "failed"})
            record["missing_fields"] = [name for name in FIELDS if not record[name]]
            record["collection_status"] = (
                "failed" if not any(s["status"] == "fetched" for s in record["sources"])
                else "partial" if record["errors"] or record["missing_fields"] else "collected"
            )
            results.append(record)
        return results


def export(records: list[dict[str, Any]], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "unimatch_ph_raw_data.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = []
    for record in records:
        for program in record["programs"] or [None]:
            row = {key: value for key, value in record.items() if key != "programs"}
            row.update({"program_name": program["name"] if program else None,
                        "strand_alignment": program["strand_alignment"] if program else None,
                        "program_source_url": program["source_url"] if program else None})
            rows.append({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                         for key, value in row.items()})
    # UTF-8 BOM helps Excel preserve Philippine peso signs and campus names.
    pd.DataFrame(rows).to_csv(directory / "unimatch_ph_raw_data.csv", index=False, encoding="utf-8-sig")


def load_config(path: Path) -> list[dict[str, Any]]:
    universities = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(universities, list) or not universities:
        raise ValueError("Configuration must be a nonempty list of universities")
    for university in universities:
        for key in ("name", "campus_location"):
            if not isinstance(university.get(key), str) or not university[key].strip():
                raise ValueError(f"Each university requires a nonempty {key}")
        if not isinstance(university.get("pages"), list) or not university["pages"]:
            raise ValueError("Each university requires at least one page")
        for page in university["pages"]:
            Page(**page)
    return universities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config/universities.json")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--university", help="Case-insensitive substring filter")
    parser.add_argument("--contact", required=True, help="Real research contact email or HTTPS project/contact URL")
    parser.add_argument("--min-delay", type=float, default=2)
    parser.add_argument("--max-delay", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--browser", action="store_true", help="Enable pages explicitly configured with render: true")
    parser.add_argument("--browser-executable", help="Optional installed Chromium path (otherwise Playwright's bundled browser)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        contact = args.contact.strip()
        if re.fullmatch(r"[^\s@()]+@[^\s@()]+\.[^\s@()]+", contact):
            contact = "mailto:" + contact
        else:
            https_url(contact)
        universities = load_config(args.config)
        if args.university:
            universities = [u for u in universities if args.university.casefold() in u["name"].casefold()]
        if not universities:
            parser.error("University filter matched no entries")
        fetcher = PoliteFetcher(f"UniMatchPHResearchBot/0.1 (+{contact})", args.min_delay,
                                args.max_delay, args.timeout, args.browser, args.browser_executable)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        parser.error(str(exc))
    try:
        records = Pipeline(fetcher).collect(universities)
        export(records, args.output_dir)
    finally:
        fetcher.close()
    failures = sum(len(r["errors"]) for r in records)
    missing = sum(len(r["missing_fields"]) for r in records)
    LOG.info("Exported %d institutions to %s; %d page failures, %d missing field groups. Human review required.",
             len(records), args.output_dir, failures, missing)
    # Partial data remains available for review, but automation must not mistake
    # failed fetches or incomplete extraction for a successful complete crawl.
    return 2 if failures or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
