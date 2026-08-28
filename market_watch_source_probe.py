# -*- coding: utf-8 -*-
"""Simple GitHub-ready probe for ARIAXONE Market Watch sources.

Purpose:
- Check whether each Market Watch source can be reached from the runner.
- Count relevant energy/oil headlines from the last 7 days.
- Save a lightweight scraping diagnostics report.

This is intentionally independent from the daily report workflow. It does not
call OpenAI, upload to Hostinger, send email, or modify daily report state.

Expected command:
    python market_watch_source_probe.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import csv
import re
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "daily_output"
CSV_OUTPUT_FILE = PROJECT_ROOT / "market_watch_source_probe_results.csv"
REPORT_OUTPUT_FILE = PROJECT_ROOT / "market_watch_source_probe_report.md"
SUMMARY_LOG_FILE = PROJECT_ROOT / "market_watch_source_probe_summary_log.csv"

LOOKBACK_DAYS = 7
REQUEST_TIMEOUT_SECONDS = 20
MAX_TITLES_PER_SOURCE = 30


@dataclass(frozen=True)
class SourceConfig:
    """One website to test."""

    name: str
    url: str
    status: str
    notes: str = ""


@dataclass
class SourceResult:
    """Scraping result for one source."""

    source: SourceConfig
    http_status: int | None = None
    issue: str = ""
    relevant_titles: list[str] = field(default_factory=list)
    raw_link_count: int = 0


# Keep this list explicit so the GitHub probe remains easy to audit.
SOURCES = [
    SourceConfig(
        name="EIA Today in Energy",
        url="https://www.eia.gov/todayinenergy/",
        status="active",
    ),
    SourceConfig(
        name="IEA",
        url="https://www.iea.org/",
        status="active",
    ),
    SourceConfig(
        name="Energy Intelligence",
        url="https://www.energyintel.com/",
        status="active",
    ),
    SourceConfig(
        name="World Oil",
        url="https://worldoil.com/",
        status="active",
    ),
    SourceConfig(
        name="Oil & Gas Journal",
        url="https://www.ogj.com/",
        status="active",
    ),
    SourceConfig(
        name="OilPrice.com",
        url="https://oilprice.com/rss/main",
        status="candidate",
        notes="Candidate RSS source for daily crude and geopolitics coverage.",
    ),
    SourceConfig(
        name="Rigzone",
        url="https://www.rigzone.com/",
        status="candidate",
        notes="Candidate source for drilling, output, and industry news.",
    ),
    SourceConfig(
        name="Hart Energy",
        url="https://www.hartenergy.com/",
        status="candidate",
        notes="Candidate source for Permian, A&D, and US upstream coverage.",
    ),
    SourceConfig(
        name="Offshore Magazine",
        url="https://www.offshore-mag.com/",
        status="candidate",
        notes="Candidate source for offshore projects and upstream developments.",
    ),
    SourceConfig(
        name="Natural Gas Intelligence",
        url="https://www.naturalgasintel.com/",
        status="candidate",
        notes="Candidate source for gas, LNG, and storage coverage.",
    ),
    SourceConfig(
        name="S&P Global Energy",
        url="https://www.spglobal.com/energy/",
        status="not active",
        notes="Mentioned in comments but not currently enabled in SOURCE_CONFIGS.",
    ),
    SourceConfig(
        name="OPEC Press Releases",
        url="https://www.opec.org/press-releases.html",
        status="not active",
        notes="Candidate source for official OPEC announcements.",
    ),
]


RELEVANCE_KEYWORDS = [
    "crude",
    "oil",
    "brent",
    "wti",
    "opec",
    "opec+",
    "gasoline",
    "distillate",
    "diesel",
    "jet fuel",
    "fuel oil",
    "refinery",
    "refining",
    "inventory",
    "inventories",
    "stock",
    "stocks",
    "supply",
    "demand",
    "exports",
    "imports",
    "sanctions",
    "tanker",
    "lng",
    "natural gas",
    "energy",
    "petroleum",
]

NAVIGATION_TITLE_WORDS = [
    "api",
    "calendar",
    "careers",
    "commercial buildings energy consumption survey",
    "contact",
    "data",
    "energy intelligence api",
    "energy intelligence premium",
    "energy intelligence store",
    "energy transition",
    "energy review",
    "engineering & science",
    "events",
    "explore",
    "international energy statistics",
    "login",
    "manufacturing energy consumption survey",
    "monthly energy review",
    "natural gas converter",
    "natural gas glossary",
    "natural gas industry faqs",
    "natural gas prices",
    "natural gas storage",
    "newsletter",
    "petroleum supply monthly",
    "press room",
    "primary energy consumption",
    "refining & processing",
    "refining & petrochem",
    "privacy",
    "register",
    "residential energy consumption survey",
    "search",
    "short-term energy outlook",
    "sign in",
    "signin",
    "subscribe",
    "subscription",
    "terms",
    "weekly natural gas storage report",
    "weekly petroleum status report",
]


DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s+"
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{4})\b",
        re.IGNORECASE,
    ),
]


def create_session() -> requests.Session:
    """Create a browser-like requests session that ignores local proxy settings."""
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def clean_text(value: str | None) -> str:
    """Normalize whitespace."""
    if not value:
        return ""
    return " ".join(value.split())


def is_relevant_title(title: str) -> bool:
    """Return True when a headline looks relevant to oil/energy markets."""
    lower_title = title.lower()
    return any(keyword in lower_title for keyword in RELEVANCE_KEYWORDS)


def looks_like_navigation_title(title: str) -> bool:
    """Return True for menu, product, survey, and utility labels."""
    lower_title = title.lower()
    ticker_pattern = r"\bwti crude\b.*\bbrent crude\b.*\bnatural gas\b"
    if re.search(ticker_pattern, lower_title):
        return True

    return any(word in lower_title for word in NAVIGATION_TITLE_WORDS)


def parse_date_from_text(text: str) -> date | None:
    """Extract one date from URL or visible text when possible."""
    clean_value = clean_text(text)

    iso_match = DATE_PATTERNS[0].search(clean_value)
    if iso_match:
        try:
            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            )
        except ValueError:
            return None

    month_day_match = DATE_PATTERNS[1].search(clean_value)
    if month_day_match:
        try:
            return datetime.strptime(month_day_match.group(0), "%B %d, %Y").date()
        except ValueError:
            return None

    day_month_match = DATE_PATTERNS[2].search(clean_value)
    if day_month_match:
        try:
            return datetime.strptime(day_month_match.group(0), "%d %B %Y").date()
        except ValueError:
            return None

    return None


def is_recent_or_undated(candidate_date: date | None, cutoff_date: date) -> bool:
    """Keep dated items from the window and visible undated homepage links."""
    if candidate_date is None:
        return True
    return candidate_date >= cutoff_date


def is_same_site_or_allowed(base_url: str, link_url: str) -> bool:
    """Avoid counting unrelated outbound links."""
    base_host = urlparse(base_url).netloc.lower().replace("www.", "")
    link_host = urlparse(link_url).netloc.lower().replace("www.", "")

    if not link_host:
        return True

    if base_host in link_host or link_host in base_host:
        return True

    # S&P links can move between energy and commodityinsights paths.
    if "spglobal.com" in base_host and "spglobal.com" in link_host:
        return True

    return False


def looks_like_service_link(url: str, title: str) -> bool:
    """Skip navigation, subscription, and utility links."""
    blocked_words = [
        "about",
        "advertis",
        "calendar",
        "contact",
        "events",
        "login",
        "newsletter",
        "privacy",
        "register",
        "search",
        "sign-in",
        "signin",
        "subscribe",
        "terms",
        "webcast",
        "whitepaper",
    ]
    lower_value = f"{url} {title}".lower()
    return any(word in lower_value for word in blocked_words) or looks_like_navigation_title(title)


def append_unique_title(titles: list[str], seen_titles: set[str], title: str) -> None:
    """Append a title once after basic quality checks."""
    clean_title = clean_text(title)
    normalized_title = clean_title.lower()

    if len(clean_title) < 18:
        return

    if looks_like_navigation_title(clean_title):
        return

    if not is_relevant_title(clean_title):
        return

    if normalized_title in seen_titles:
        return

    seen_titles.add(normalized_title)
    titles.append(clean_title)


def extract_eia_today_in_energy_titles(html: str) -> tuple[list[str], int]:
    """Extract real EIA Today in Energy article card titles."""
    soup = BeautifulSoup(html, "html.parser")
    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)
    titles = []
    seen_titles = set()
    cards = soup.select(".tie-article")

    for article in cards:
        date_element = article.select_one(".date")
        headline_element = article.select_one("h1 a")

        if date_element is None or headline_element is None:
            continue

        article_date = parse_date_from_text(date_element.get_text(" "))
        if not is_recent_or_undated(article_date, cutoff_date):
            continue

        append_unique_title(titles, seen_titles, headline_element.get_text(" "))

    return titles[:MAX_TITLES_PER_SOURCE], len(cards)


def extract_iea_titles(html: str) -> tuple[list[str], int]:
    """Extract IEA homepage story cards, matching the production scraper shape."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("article.o-hero-latest__listing")
    titles = []
    seen_titles = set()

    for article in cards:
        headline_element = article.select_one("h2")
        if headline_element is None:
            continue
        append_unique_title(titles, seen_titles, headline_element.get_text(" "))

    return titles[:MAX_TITLES_PER_SOURCE], len(cards)


def is_energy_intelligence_article_url(url: str) -> bool:
    """Return True for Energy Intelligence article URLs, not service pages."""
    lower_url = url.lower()

    if "energyintel.com" not in lower_url:
        return False

    blocked_words = [
        "service",
        "research",
        "data",
        "store",
        "forum",
        "my-ei",
        "profile",
        "subscription",
        "register",
        "sign-in",
        "login",
    ]
    if any(word in lower_url for word in blocked_words):
        return False

    return bool(re.search(r"/000001", lower_url) or re.search(r"/\d{4}-\d{2}-\d{2}/", lower_url))


def extract_energy_intelligence_titles(html: str, source: SourceConfig) -> tuple[list[str], int]:
    """Extract Energy Intelligence article titles without product/navigation links."""
    soup = BeautifulSoup(html, "html.parser")
    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)
    titles = []
    seen_titles = set()
    raw_link_count = 0

    for link in soup.select("a[href]"):
        raw_link_count += 1
        title = clean_text(link.get_text(" "))
        article_url = urljoin(source.url, link.get("href", ""))

        if not is_energy_intelligence_article_url(article_url):
            continue

        candidate_date = parse_date_from_text(article_url)
        if not is_recent_or_undated(candidate_date, cutoff_date):
            continue

        append_unique_title(titles, seen_titles, title)

        if len(titles) >= MAX_TITLES_PER_SOURCE:
            break

    return titles, raw_link_count


def extract_rss_titles(xml_text: str) -> tuple[list[str], int]:
    """Extract relevant titles from a simple RSS feed."""
    titles = []
    seen_titles = set()
    raw_item_count = 0
    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return titles, raw_item_count

    for item in root.findall(".//item"):
        raw_item_count += 1
        title = clean_text(item.findtext("title"))
        pub_date_text = clean_text(item.findtext("pubDate"))
        candidate_date = parse_date_from_text(pub_date_text)

        if not is_recent_or_undated(candidate_date, cutoff_date):
            continue

        append_unique_title(titles, seen_titles, title)

        if len(titles) >= MAX_TITLES_PER_SOURCE:
            break

    return titles, raw_item_count


def extract_relevant_titles(html: str, source: SourceConfig) -> tuple[list[str], int]:
    """Extract relevant title text using source-aware rules when available."""
    if source.url.lower().endswith((".rss", "/rss/main")) or "<rss" in html[:500].lower():
        return extract_rss_titles(html)

    if source.name == "EIA Today in Energy":
        return extract_eia_today_in_energy_titles(html)

    if source.name == "IEA":
        return extract_iea_titles(html)

    if source.name == "Energy Intelligence":
        return extract_energy_intelligence_titles(html, source)

    soup = BeautifulSoup(html, "html.parser")
    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)
    titles = []
    seen_titles = set()
    raw_link_count = 0

    for link in soup.select("a[href]"):
        raw_link_count += 1
        title = clean_text(link.get_text(" "))
        if len(title) < 18:
            continue

        link_url = urljoin(source.url, link.get("href", ""))
        if not is_same_site_or_allowed(source.url, link_url):
            continue

        if looks_like_service_link(link_url, title):
            continue

        if not is_relevant_title(title):
            continue

        container = link.find_parent(["article", "li", "div", "section"])
        context_text = clean_text(container.get_text(" ") if container else "")
        candidate_date = parse_date_from_text(f"{link_url} {context_text}")
        if not is_recent_or_undated(candidate_date, cutoff_date):
            continue

        normalized_title = title.lower()
        if normalized_title in seen_titles:
            continue

        seen_titles.add(normalized_title)
        titles.append(title)

        if len(titles) >= MAX_TITLES_PER_SOURCE:
            break

    return titles, raw_link_count


def diagnose_issue(result: SourceResult) -> str:
    """Create a short issue/solution note for one source."""
    if result.issue:
        return result.issue

    if result.http_status in {401, 403}:
        return (
            "Access blocked or forbidden. Possible solutions: use an official RSS/API "
            "if available, replace with a public source, or keep this source as optional."
        )

    if result.http_status in {429}:
        return (
            "Rate limited. Possible solutions: reduce frequency, add backoff, or cache "
            "the last successful source result."
        )

    if result.http_status and result.http_status >= 500:
        return (
            "Source server error. Possible solutions: retry later and keep the daily "
            "report running without this source."
        )

    if result.raw_link_count == 0:
        return (
            "No links found. The page may require JavaScript rendering or changed HTML. "
            "Possible solution: use RSS/API or a source-specific parser."
        )

    if not result.relevant_titles:
        return (
            "Reachable, but no relevant recent titles were detected. Possible solutions: "
            "expand the lookback window, tune keywords, or add a source-specific parser."
        )

    return "OK."


def scrape_source(session: requests.Session, source: SourceConfig) -> SourceResult:
    """Fetch one source and return a lightweight diagnostic result."""
    result = SourceResult(source=source)

    try:
        response = session.get(source.url, timeout=REQUEST_TIMEOUT_SECONDS)
        result.http_status = response.status_code

        if response.status_code >= 400:
            result.issue = diagnose_issue(result)
            return result

        titles, raw_link_count = extract_relevant_titles(response.text, source)
        result.relevant_titles = titles
        result.raw_link_count = raw_link_count
        result.issue = diagnose_issue(result)
        return result

    except requests.exceptions.Timeout:
        result.issue = (
            "Request timed out. Possible solutions: keep a short timeout, retry once, "
            "or use cached headlines when this source is slow."
        )
    except requests.exceptions.RequestException as error:
        result.issue = (
            f"Network/request failure: {error}. Possible solutions: verify DNS/TLS, "
            "ignore proxy variables, and keep this source optional."
        )

    return result


def write_csv(results: list[SourceResult]) -> None:
    """Save machine-readable probe results."""
    with CSV_OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "source",
                "status",
                "url",
                "http_status",
                "raw_link_count",
                "relevant_news_count",
                "titles",
                "issue_or_solution",
                "notes",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.source.name,
                    result.source.status,
                    result.source.url,
                    result.http_status or "",
                    result.raw_link_count,
                    len(result.relevant_titles),
                    " | ".join(result.relevant_titles),
                    result.issue,
                    result.source.notes,
                ]
            )


def write_markdown_report(results: list[SourceResult]) -> None:
    """Save a human-readable probe report."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Market Watch Source Probe",
        "",
        f"Generated: {generated_at}",
        f"Lookback window: last {LOOKBACK_DAYS} days",
        "",
        "This dummy agent is for GitHub readiness testing only. It does not modify "
        "the daily report, publish files, or send email.",
        "",
        "## Summary",
        "",
        "| Source | Status | HTTP | Relevant News | Issue / Suggested Fix |",
        "|---|---:|---:|---:|---|",
    ]

    for result in results:
        lines.append(
            "| "
            f"{result.source.name} | "
            f"{result.source.status} | "
            f"{result.http_status or 'n/a'} | "
            f"{len(result.relevant_titles)} | "
            f"{result.issue.replace('|', '/')}"
            " |"
        )

    lines.extend(["", "## Titles By Source", ""])

    for result in results:
        lines.extend(
            [
                f"### {result.source.name}",
                "",
                f"- Status: {result.source.status}",
                f"- URL: {result.source.url}",
                f"- HTTP status: {result.http_status or 'n/a'}",
                f"- Relevant news count: {len(result.relevant_titles)}",
                f"- Scraping note: {result.issue}",
            ]
        )
        if result.source.notes:
            lines.append(f"- Source note: {result.source.notes}")

        if result.relevant_titles:
            lines.append("")
            for title in result.relevant_titles:
                lines.append(f"- {title}")
        else:
            lines.append("")
            lines.append("- No relevant titles detected.")

        lines.append("")

    REPORT_OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")


def append_summary_log(results: list[SourceResult]) -> None:
    """Append one compact source summary per run for easy comparison.

    The normal CSV and Markdown files show the latest probe snapshot. This log
    keeps the history: every run adds one row per source with the same summary
    fields used in the Markdown table.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    file_exists = SUMMARY_LOG_FILE.is_file()

    with SUMMARY_LOG_FILE.open("a", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow(
                [
                    "run_timestamp_utc",
                    "source",
                    "status",
                    "http_status",
                    "relevant_news_count",
                    "issue_or_suggested_fix",
                ]
            )

        for result in results:
            writer.writerow(
                [
                    generated_at,
                    result.source.name,
                    result.source.status,
                    result.http_status or "n/a",
                    len(result.relevant_titles),
                    result.issue,
                ]
            )


def main() -> int:
    """Run the Market Watch source probe."""
    session = create_session()
    results = []

    print("Checking Market Watch sources...")
    for source in SOURCES:
        print(f"- {source.name} ({source.status})")
        result = scrape_source(session, source)
        results.append(result)
        print(
            f"  HTTP={result.http_status or 'n/a'} "
            f"relevant_news={len(result.relevant_titles)}"
        )

    write_csv(results)
    write_markdown_report(results)
    append_summary_log(results)

    print(f"CSV saved to: {CSV_OUTPUT_FILE}")
    print(f"Report saved to: {REPORT_OUTPUT_FILE}")
    print(f"Summary log updated: {SUMMARY_LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
