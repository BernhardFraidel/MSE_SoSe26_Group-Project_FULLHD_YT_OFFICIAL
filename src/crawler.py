from __future__ import annotations

import time
import urllib.robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from langdetect import detect

from src.utils import (
    is_probably_html_url,
    normalize_url,
    now_utc_iso,
    project_path,
    read_json,
    short_snippet,
    write_json,
)


USER_AGENT = "MSE-Tuebingen-StudentCrawler | Contact: janis.weller@student.uni-tuebingen.de"


def load_seed_urls(seeds_path: str | Path = project_path("seeds.json")) -> list[str]:
    """Read the flat list of seed URLs from seeds.json and normalize them (no priority or title fields)."""
    data = read_json(seeds_path, {"seeds": []})
    seeds = data.get("seeds", [])
    print(f"[crawl] loaded {len(seeds)} seed urls from {seeds_path}")
    return [normalize_url(seed) for seed in seeds if seed]


def _robots_allowed(url: str, user_agent: str, cache: dict[str, urllib.robotparser.RobotFileParser]) -> bool:
    """Check robots.txt (cached per host) to see if we're allowed to fetch this URL."""
    # Fetch and cache the robots.txt parser for this host, so we only download it once
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    if robots_url not in cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
        except Exception:
            # If robots.txt can't be fetched/parsed, default to allowing the crawl
            return True
        cache[robots_url] = parser
    
    # Use the cached parser to check if this specific URL is disallowed
    try:
        return cache[robots_url].can_fetch(user_agent, url)
    except Exception:
        return True


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Pull all usable HTML links out of a parsed page, normalized against the base URL."""
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "")
        # Skip non-navigable links (email, phone, inline scripts)
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        # Resolve relative links to absolute URLs and keep only likely HTML pages
        normalized = normalize_url(href, base_url)
        if is_probably_html_url(normalized):
            links.append(normalized)
    # Dedupe and return a stable, sorted order
    return sorted(set(links))


def _url_key(url: str) -> str:
    """Normalize a URL into a comparable key (used for dedup) by stripping the trailing slash."""
    return normalize_url(url).rstrip("/")


def _extract_body(soup: BeautifulSoup) -> str:
    """Strip non-content tags and return the visible text of the page's main/body content."""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup
    return " ".join(main.get_text(" ", strip=True).split())


def _detect_language(text: str, html_lang: str) -> str:
    """Determine the page language, preferring the HTML lang attribute and falling back to langdetect."""
    if html_lang:
        return html_lang.lower().split("-")[0]
    try:
        return detect(short_snippet(text, 1000))
    except Exception:
        return "unknown"


def _is_tuebingen_related(url: str, title: str, body: str) -> bool:
    """Check whether the URL, title, or body text mentions Tuebingen in any common spelling variant."""
    text = f"{url} {title} {body}".lower()
    return (
        "tuebingen" in text
        or "tübingen" in text
        or "tubingen" in text
    )


def _looks_english(url: str, language: str, body: str) -> bool:
    """Decide if a page is English, trusting langdetect first and the /en/ URL path as a fallback."""
    if language == "en":
        return True
    if language and language != "unknown":
        return False
    return "/en" in urlparse(url).path.lower()


def crawl(
    seeds_path: str | Path = project_path("seeds.json"),
    raw_pages_path: str | Path = project_path("data", "raw_pages.json"),
    frontier_path: str | Path = project_path("data", "frontier.json"),
    visited_path: str | Path = project_path("data", "visited.json"),
    max_pages: int = 20,
    timeout: float = 8.0,
    polite_delay: float = 0.6,
) -> dict[str, Any]:
    """Run the crawl loop: fetch frontier URLs, filter/save relevant pages, and persist crawler state."""
    # Resolve output paths
    raw_pages_path = Path(raw_pages_path)
    frontier_path = Path(frontier_path)
    visited_path = Path(visited_path)

    # Load any previously saved pages, and resume the frontier from disk (or from 0)
    raw = read_json(raw_pages_path, {"pages": []})
    pages = raw.get("pages", [])
    frontier = read_json(frontier_path, [])
    if not frontier:
        frontier = load_seed_urls(seeds_path)

    # Load which URLs were already visited, so we don't refetch them across runs
    visited_data = read_json(visited_path, {"visited": []})
    visited_entries = visited_data.get("visited", [])
    visited_urls = {item.get("url") for item in visited_entries}
    # Build a lookup of already-saved pages by canonical URL, so duplicate content is skipped
    known_page_keys = {
        _url_key(page.get("canonical_url") or page.get("url", ""))
        for page in pages
        if page.get("canonical_url") or page.get("url")
    }
    # Set up per-run state (robots.txt cache, counters, HTTP session)
    robot_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
    attempted = 0
    saved = 0
    next_doc_id = max([int(page.get("doc_id", -1)) for page in pages] + [-1]) + 1
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"[crawl] starting crawl: {len(frontier)} urls in frontier, {len(pages)} pages already saved, max_pages={max_pages}")

    while frontier and saved < max_pages:
        # Dequeue the next candidate URL (normalize_url also upgrades http:// to https://, so we don't fetch the same page twice just because it appears in both schemes)
        url = normalize_url(frontier.pop(0))
        if url in visited_urls or not is_probably_html_url(url):
            continue
        attempted += 1
        print(f"[crawl] #{attempted} fetching: {url}")

        status_code = None
        try:
            # Respect robots.txt before fetching anything
            if not _robots_allowed(url, USER_AGENT, robot_cache):
                print(f"[crawl]   blocked by robots.txt: {url}")
                visited_entries.append({"url": url, "visited_at": now_utc_iso(), "status_code": "robots_blocked"})
                visited_urls.add(url)
                continue

            # Fetch the page and record that we've visited it, regardless of outcome
            response = session.get(url, timeout=timeout, allow_redirects=True)
            status_code = response.status_code
            fetched_url = normalize_url(response.url)
            content_type = response.headers.get("content-type", "")
            print(f"[crawl]   status={status_code} content-type={content_type}")
            visited_entries.append({"url": url, "visited_at": now_utc_iso(), "status_code": status_code})
            visited_urls.add(url)

            # Only continue parsing successful, actual HTML responses
            if status_code != 200 or "text/html" not in content_type.lower():
                print(f"[crawl]   skipping: not a 200 html response")
                continue

            # Parse the page and pull out everything we need
            soup = BeautifulSoup(response.text, "html.parser")
            canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
            canonical_url = normalize_url(canonical.get("href"), fetched_url) if canonical and canonical.get("href") else fetched_url
            title = soup.title.get_text(" ", strip=True) if soup.title else fetched_url
            headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if h.get_text(strip=True)]
            body = _extract_body(soup)
            outgoing_links = _extract_links(soup, fetched_url)
            html_lang = soup.html.get("lang", "") if soup.html else ""
            language = _detect_language(body, html_lang)
            is_related = _is_tuebingen_related(canonical_url, title, body)
            print(f"[crawl]   title='{title[:60]}' language={language} tuebingen_related={is_related} links_found={len(outgoing_links)}")

            # Queue newly discovered links for later, regardless of whether this page is saved
            for link in outgoing_links:
                if link not in visited_urls and link not in frontier and len(frontier) < 1000:
                    frontier.append(link)

            # Only keep pages that are actually Tuebingen-related, English, and not already saved
            if not is_related or not _looks_english(canonical_url, language, body):
                print(f"[crawl]   skipping: not tuebingen-related or not english")
                continue
            canonical_key = _url_key(canonical_url)
            if canonical_key in known_page_keys:
                print(f"[crawl]   skipping: canonical url already saved ({canonical_url})")
                continue

            # Store the page
            pages.append(
                {
                    "doc_id": next_doc_id,
                    "url": url,
                    "fetched_url": fetched_url,
                    "canonical_url": canonical_url,
                    "title": title,
                    "headings": headings[:20],
                    "body": body,
                    "outgoing_links": outgoing_links[:200],
                    "language": language,
                    "is_tuebingen_related": is_related,
                    "crawl_time": now_utc_iso(),
                }
            )
            known_page_keys.add(canonical_key)
            print(f"[crawl]   saved as doc_id={next_doc_id} ({saved + 1}/{max_pages})")
            next_doc_id += 1
            saved += 1
        except requests.RequestException as exc:
            # Network/timeout errors: mark as visited so we don't keep retrying a dead URL
            print(f"[crawl]   request error: {exc}")
            visited_entries.append({"url": url, "visited_at": now_utc_iso(), "status_code": status_code or "request_error"})
            visited_urls.add(url)
        finally:
            # Always wait between requests, even on failure, to stay polite to the server
            time.sleep(polite_delay)

    # Persist crawler state to disk so subsequent runs can resume where this one left off
    write_json(raw_pages_path, {"pages": pages})
    write_json(frontier_path, frontier)
    write_json(visited_path, {"visited": visited_entries})
    summary = {
        "step": "crawling",
        "saved_pages": saved,
        "total_pages": len(pages),
        "attempted_urls": attempted,
        "frontier_size": len(frontier),
        "visited_size": len(visited_entries),
        "timeout": timeout,
        "polite_delay": polite_delay,
    }
    write_json(raw_pages_path.parent / "crawl_summary.json", summary)
    print(f"[crawl] done: saved={saved} attempted={attempted} frontier_remaining={len(frontier)}")
    return summary
