from __future__ import annotations

import heapq
import logging
import random
import threading
import time
import urllib.robotparser
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from langdetect import detect

from src.utils import (
    get_domain,
    is_probably_html_url,
    normalize_url,
    now_utc_iso,
    project_path,
    read_json,
    short_snippet,
    write_json,
)


USER_AGENT = "MSE-Tuebingen-StudentCrawler | Contact: janis.weller@student.uni-tuebingen.de"

logger = logging.getLogger("crawler")
logger.setLevel(logging.INFO)


class _WorkerLineFilter(logging.Filter):
    """Only let genuine per-worker log lines (e.g. "worker=2 t+123ms ...") through."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().startswith("worker=")


def _setup_logging(log_path: str | Path) -> None:
    """Point the crawler's logger at log_path as well as the console."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_WorkerLineFilter())
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    logger.propagate = False


def load_seed_urls(seeds_path: str | Path = project_path("seeds.json")) -> list[str]:
    """Read the flat list of seed URLs from seeds.json and normalize them (no priority or title fields)."""
    data = read_json(seeds_path, {"seeds": []})
    seeds = data.get("seeds", [])
    logger.info(f"loaded {len(seeds)} seed urls from {seeds_path}")
    return [normalize_url(seed) for seed in seeds if seed]


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
    """Determine the page language."""
    try:
        detected = detect(short_snippet(text, 1000))
    except Exception:
        detected = None

    if detected:
        return detected
    if html_lang:
        return html_lang.lower().split("-")[0]
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


def _format_elapsed(seconds: float) -> str:
    """Format a duration in seconds as a compact H:MM:SS-ish string."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class _FrontierLevel:
    """The domain-aware frontier for a single priority level (high or low)."""

    __slots__ = ("domains", "heap", "url_set")

    def __init__(self) -> None:
        self.domains: dict[str, deque[str]] = {}
        self.heap: list[tuple[float, float, str]] = []
        self.url_set: set[str] = set()


class CrawlerState:
    """All mutable state shared across worker threads, guarded by a single lock"""

    def __init__(
        self,
        frontier_high: list[str],
        frontier_low: list[str],
        pages: list[dict[str, Any]],
        visited_entries: list[dict[str, Any]],
        max_pages: int,
        polite_delay: float,
        checkpoint_every: int,
        domain_next_time: dict[str, float] | None = None,
        elapsed_offset: float = 0.0,
    ) -> None:
        self.start_time = time.time() - elapsed_offset
        self.lock = threading.Lock()
        # Guards the writing of checkpoint files.
        self.checkpoint_lock = threading.Lock()

        self.frontier_high = _FrontierLevel()
        self.frontier_low = _FrontierLevel()

        # domain -> earliest timestamp (time.time()) at which we're allowed to fetch it again.
        self.domain_next_time: dict[str, float] = dict(domain_next_time or {})

        for url in frontier_high:
            self._enqueue(url, self.frontier_high)
        for url in frontier_low:
            self._enqueue(url, self.frontier_low)

        self.pages = pages
        self.visited_entries = visited_entries
        self.visited_urls = {item.get("url") for item in visited_entries}
        self.known_page_keys = {
            _url_key(page.get("canonical_url") or page.get("url", ""))
            for page in pages
            if page.get("canonical_url") or page.get("url")
        }
        self.next_doc_id = max([int(page.get("doc_id", -1)) for page in pages] + [-1]) + 1
        self.max_pages = max_pages
        self.polite_delay = polite_delay
        self.checkpoint_every = checkpoint_every
        self.next_checkpoint_at = checkpoint_every if checkpoint_every > 0 else None

        self.attempted = 0
        self.saved = 0
        self.in_flight = 0  # URLs currently being fetched/processed by a worker
        self.in_flight_urls: set[str] = set()
        self.interrupted = False
        self.stop_event = threading.Event()

        # robots.txt cache is read/written far less often than the frontier, so it gets its own lock instead of contending with the hot path above.
        self.robots_lock = threading.Lock()
        self.robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


    def _enqueue(self, url: str, level: _FrontierLevel) -> None:
        """Add a single url to one priority level's domain-aware frontier."""
        if url in level.url_set:
            return
        try:
            domain = get_domain(url)
        except Exception:
            # Malformed URL - drop it rather than let it poison the frontier.
            return

        queue = level.domains.get(domain)
        if queue is None:
            queue = deque()
            level.domains[domain] = queue
            heapq.heappush(level.heap, (self.domain_next_time.get(domain, 0.0), random.random(), domain))

        queue.append(url)
        level.url_set.add(url)

    @staticmethod
    def _frontier_size(level: _FrontierLevel) -> int:
        """Total number of urls currently waiting across all domains at one priority level."""
        return sum(len(queue) for queue in level.domains.values())

    def _claim_from_heap(self, level: _FrontierLevel, now: float) -> str | None:
        """Pop one ready url from a single priority level's domain-aware frontier."""
        domains, url_set, heap = level.domains, level.url_set, level.heap
        while heap:
            stored_time, tiebreak, domain = heap[0]
            true_time = self.domain_next_time.get(domain, 0.0)
            if true_time > stored_time:
                # Stale entry - someone updated this domain's cooldown after we scheduled it. Fix it in place (keep the same tiebreak) and recheck from the top.
                heapq.heapreplace(heap, (true_time, tiebreak, domain))
                continue

            if stored_time > now:
                # Earliest domain in this heap still isn't ready; nothing else in the heap can be ready sooner, so stop here.
                return None

            queue = domains.get(domain)
            if not queue:
                # Domain drained already -> clean up and keep looking.
                heapq.heappop(heap)
                domains.pop(domain, None)
                continue

            # Domain is ready: pop its heap entry now so no other worker can claim from it until we push it back below (or it's found empty).
            heapq.heappop(heap)

            claimed_url: str | None = None
            while queue:
                url = queue.popleft()
                url_set.discard(url)
                if url in self.visited_urls or not is_probably_html_url(url):
                    continue
                claimed_url = url
                break

            if not queue:
                domains.pop(domain, None)

            if claimed_url is None:
                # Every url we found for this domain was stale/invalid; the domain itself isn't on cooldown, so if it still has urls left, put it
                # back at the front of the heap (unchanged cooldown/tiebreak) and keep looking at the next domain instead of returning empty-handed.
                if queue:
                    heapq.heappush(heap, (stored_time, tiebreak, domain))
                continue

            # Reserve this domain's next slot immediately, so a second worker can't grab another URL for the same domain before this fetch starts.
            self.domain_next_time[domain] = now + self.polite_delay
            if queue:
                heapq.heappush(heap, (self.domain_next_time[domain], random.random(), domain))
            self.in_flight += 1
            self.in_flight_urls.add(claimed_url)
            self.attempted += 1
            return claimed_url

        return None

    def _earliest_ready_time(self, level: _FrontierLevel) -> float | None:
        """Peek (without popping) the true next_allowed_time of the earliest domain in a level's heap."""
        if not level.heap:
            return None
        stored_time, _tiebreak, domain = level.heap[0]
        return max(stored_time, self.domain_next_time.get(domain, 0.0))

    def claim_next_url(self) -> tuple[str | None, str]:
        """Try to claim the next URL that is both in a frontier and not on its domain's cooldown. frontier_high (Tuebingen-related discoveries) is always scanned before frontier_low.
        Returns (url, "ok") if a URL was claimed (this also marks it in-flight and reserves its domains cooldown slot), (None, "wait") if nothing is ready yet
        but there's still work outstanding, or (None, "done") if the crawl should stop (max_pages reached, or both frontiers are empty with no other worker
        in-flight that could still add to them).
        """
        with self.lock:
            if self.stop_event.is_set() or len(self.pages) >= self.max_pages:
                self.stop_event.set()
                return None, "done"

            now = time.time()
            claimed_url = self._claim_from_heap(self.frontier_high, now)
            if claimed_url is None:
                claimed_url = self._claim_from_heap(self.frontier_low, now)

            if claimed_url is not None:
                return claimed_url, "ok"

            if not self.frontier_high.domains and not self.frontier_low.domains and self.in_flight == 0:
                self.stop_event.set()
                return None, "done"
            return None, "wait"

    def seconds_until_next_domain(self) -> float:
        """How long a worker should sleep before it's worth calling claim_next_url() again."""
        with self.lock:
            now = time.time()
            candidates = [
                t
                for t in (self._earliest_ready_time(self.frontier_high), self._earliest_ready_time(self.frontier_low))
                if t is not None
            ]
            if not candidates:
                return 0.0
            soonest = min(candidates)
            return max(0.0, soonest - now)

    def finish_url(self, domain: str, url: str, finished_at: float) -> None:
        """Release the in-flight slot and make sure the domain's cooldown reflects when the fetch actually completed."""
        with self.lock:
            self.in_flight -= 1
            self.in_flight_urls.discard(url)
            candidate = finished_at + self.polite_delay
            if candidate > self.domain_next_time.get(domain, 0.0):
                self.domain_next_time[domain] = candidate

    def add_links(self, links: list[str], is_related: bool) -> None:
        """Queue newly discovered links. If the page they were found on is Tuebingen-related they go into frontier_high; otherwise frontier_low."""
        with self.lock:
            target_level = self.frontier_high if is_related else self.frontier_low
            for link in links:
                if (
                    link not in self.visited_urls
                    and link not in self.frontier_high.url_set
                    and link not in self.frontier_low.url_set
                    and link not in self.in_flight_urls
                ):
                    self._enqueue(link, target_level)

    def record_visited(self, url: str, status_code: Any) -> None:
        with self.lock:
            self.visited_entries.append({"url": url, "visited_at": now_utc_iso(), "status_code": status_code})
            self.visited_urls.add(url)

    def try_save_page(self, canonical_url: str, page: dict[str, Any]) -> tuple[str, int, int, bool]:
        """Try to save a page.

        Returns (status, doc_id, total_pages, over_cap):
        - status is "saved", or "duplicate" if the canonical URL was already saved.
        - doc_id is the assigned doc id (only meaningful when status == "saved").
        - total_pages is len(self.pages) after this call.
        - over_cap is True if this save pushed the file above max_pages.

        Note: this does NOT reject a save just because max_pages was already reached. A worker
        may have already fetched and parsed a page by the time another worker's save hits the cap and sets
        stop_event. Discarding that already- completed page would silently waste the work and drop content
        wed otherwise want, so it is saved anyway. This means the file can end up with a few more pages than max_pages,
        bounded by roughly the number of workers that were in-flight at the moment the cap was hit.
        """
        with self.lock:
            canonical_key = _url_key(canonical_url)
            if canonical_key in self.known_page_keys:
                return "(any common spelling variant), duplicate", -1, len(self.pages), False
            page["doc_id"] = self.next_doc_id
            self.pages.append(page)
            self.known_page_keys.add(canonical_key)
            self.next_doc_id += 1
            self.saved += 1
            total_pages = len(self.pages)
            over_cap = total_pages > self.max_pages
            if total_pages >= self.max_pages:
                self.stop_event.set()
            return "saved", page["doc_id"], total_pages, over_cap

    def robots_allowed(self, url: str, user_agent: str, session: requests.Session, timeout: float) -> bool:
        """Check robots.txt (cached per host) to see if we're allowed to fetch this URL."""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        with self.robots_lock:
            parser = self.robots_cache.get(robots_url)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            try:
                response = session.get(robots_url, timeout=timeout)
            except requests.RequestException:
                # If robots.txt can't be fetched within the timeout, default to allowing the crawl
                return True
            # Mirror RobotFileParser.read() own status-code handling
            if response.status_code in (401, 403):
                parser.disallow_all = True
            elif response.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(response.text.splitlines())
            with self.robots_lock:
                self.robots_cache[robots_url] = parser
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:
            return True

    def should_checkpoint(self) -> bool:
        """Decide whether the calling worker is the one that should run the next checkpoint."""
        if self.next_checkpoint_at is None:
            return False
        with self.lock:
            if self.attempted >= self.next_checkpoint_at:
                self.next_checkpoint_at += self.checkpoint_every
                return True
            return False

    def snapshot_for_checkpoint(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, list[str]], dict[str, float], list[dict[str, Any]]]:
        """Take a consistent, lock-protected copy of everything that gets written to disk."""
        with self.lock:
            frontier_high = {domain: list(queue) for domain, queue in self.frontier_high.domains.items() if queue}
            frontier_low = {domain: list(queue) for domain, queue in self.frontier_low.domains.items() if queue}
            return (
                list(self.pages),
                frontier_high,
                frontier_low,
                dict(self.domain_next_time),
                list(self.visited_entries),
            )


def _frontier_total(frontier: dict[str, list[str]]) -> int:
    """Total number of urls across all domains in a checkpointed (domain -> urls) frontier dict."""
    return sum(len(urls) for urls in frontier.values())


def _flatten_frontier(value: dict[str, list[str]]) -> list[str]:
    """Flatten a checkpointed frontier ({domain: [urls...]}) into a flat url list for CrawlerState's constructor."""
    flat: list[str] = []
    for urls in value.values():
        flat.extend(urls)
    return flat


def _save_state(
    raw_pages_path: Path,
    frontier_path: Path,
    visited_path: Path,
    pages: list[dict[str, Any]],
    frontier_high: dict[str, list[str]],
    frontier_low: dict[str, list[str]],
    domain_next_time: dict[str, float],
    visited_entries: list[dict[str, Any]],
) -> None:
    """Persist the current crawler state (pages/frontier/visited) to disk.

    Both frontier queues are written into a single frontier.json file, as
    {"frontier_high": {domain: [urls...]}, "frontier_low": {domain: [urls...]},
    "domain_next_time": {domain: timestamp}} - enough to reconstruct each domain's
    url queue plus its heap position (next_allowed_time) on resume.
    """
    write_json(raw_pages_path, {"pages": pages})
    write_json(
        frontier_path,
        {"frontier_high": frontier_high, "frontier_low": frontier_low, "domain_next_time": domain_next_time},
    )
    write_json(visited_path, {"visited": visited_entries})


def _process_url(state: CrawlerState, session: requests.Session, worker_id: int, url: str, timeout: float) -> None:
    """Fetch, parse, and (maybe) save a single URL. Always releases the URL's in-flight/cooldown slot when done."""
    domain = "unknown"
    status_code: Any = None
    reason = "unknown"
    visited_recorded = False
    try:
        domain = get_domain(url)
        if not state.robots_allowed(url, USER_AGENT, session, timeout):
            state.record_visited(url, "robots_blocked")
            visited_recorded = True
            reason = "skipped: blocked by robots.txt"
            return

        response = session.get(url, timeout=timeout, allow_redirects=True)
        status_code = response.status_code
        fetched_url = normalize_url(response.url)
        content_type = response.headers.get("content-type", "")
        state.record_visited(url, status_code)
        visited_recorded = True

        if status_code != 200 or "text/html" not in content_type.lower():
            reason = f"skipped: not a 200 html response (status={status_code}, content-type={content_type})"
            return

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

        # Queue newly discovered links for later, regardless of whether this page is saved.
        state.add_links(outgoing_links, is_related)

        if not is_related or not _looks_english(canonical_url, language, body):
            reason = "skipped: not tuebingen-related or not english"
            return

        page = {
            "url": url,
            "fetched_url": fetched_url,
            "canonical_url": canonical_url,
            "title": title,
            "headings": headings,
            "body": body,
            "outgoing_links": outgoing_links,
            "language": language,
            "is_tuebingen_related": is_related,
            "crawl_time": now_utc_iso(),
        }
        status, doc_id, total_pages, over_cap = state.try_save_page(canonical_url, page)
        if status == "saved":
            reason = f"saved: doc_id={doc_id} ({total_pages}/{state.max_pages} pages)"
            if over_cap:
                reason += " [over max_pages cap, already-fetched work kept]"
        else:
            reason = f"skipped: canonical url already saved ({canonical_url})"
    except requests.RequestException as exc:
        reason = f"skipped: request error: {exc}"
        if not visited_recorded:
            state.record_visited(url, status_code or "request_error")
    except Exception as exc:
        reason = f"skipped: unexpected error: {type(exc).__name__}: {exc}"
        if not visited_recorded:
            state.record_visited(url, status_code or "error")
    finally:
        elapsed_ms = (time.time() - state.start_time) * 1000
        logger.info(f"worker={worker_id} t+{elapsed_ms:.0f}ms domain={domain} url={url} {reason}")
        state.finish_url(domain, url, time.time())


def _worker_loop(state: CrawlerState, worker_id: int, timeout: float, checkpoint_paths: tuple[Path, Path, Path]) -> None:
    """Main loop for a single worker thread: repeatedly claim a ready URL and process it."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    while not state.stop_event.is_set():
        try:
            url, status = state.claim_next_url()
        except Exception as exc:
            # claim_next_url() guards its own risky calls, so this shouldn't fire -
            # but an uncaught exception here would silently kill this thread, and a
            # dead worker permanently loses its share of concurrency. Log and retry
            # rather than let the thread disappear.
            logger.info(f"worker={worker_id} error claiming next url: {type(exc).__name__}: {exc}")
            time.sleep(0.05)
            continue

        if status == "done":
            return
        if status == "wait":
            # Nothing is ready right now (either the frontier is empty but other workers are still in-flight and might add to it, or every candidate domain is on cooldown).
            time.sleep(max(0.01, state.seconds_until_next_domain()))
            continue

        assert url is not None
        try:
            _process_url(state, session, worker_id, url, timeout)
        except Exception as exc:
            logger.info(f"worker={worker_id} unexpected crash processing {url}: {type(exc).__name__}: {exc}")

        try:
            if state.should_checkpoint():
                with state.checkpoint_lock:
                    raw_pages_path, frontier_path, visited_path = checkpoint_paths
                    pages, frontier_high, frontier_low, domain_next_time, visited_entries = state.snapshot_for_checkpoint()
                    _save_state(
                        raw_pages_path,
                        frontier_path,
                        visited_path,
                        pages,
                        frontier_high,
                        frontier_low,
                        domain_next_time,
                        visited_entries,
                    )
                    logger.info(
                        f"checkpoint saved (attempted={state.attempted}, saved={state.saved}, "
                        f"frontier_high={_frontier_total(frontier_high)}, frontier_low={_frontier_total(frontier_low)})"
                    )
        except Exception as exc:
            # Disk-full, permissions, or a stray non-serializable value shouldn't be able to kill a worker thread.
            logger.info(f"worker={worker_id} checkpoint save failed: {type(exc).__name__}: {exc}")


def crawl(
    seeds_path: str | Path = project_path("seeds.json"),
    raw_pages_path: str | Path = project_path("data", "raw_pages.json"),
    frontier_path: str | Path = project_path("data", "frontier.json"),
    visited_path: str | Path = project_path("data", "visited.json"),
    log_path: str | Path = project_path("crawl.log"),
    max_pages: int = 20,
    timeout: float = 8.0,
    polite_delay: float = 0.6,
    checkpoint_every: int = 5,
    workers: int = 4,
    fresh: bool = False,
) -> dict[str, Any]:
    """Run the crawl: fetch frontier URLs (frontier_high first, then frontier_low) with `workers` concurrent
    threads, filter/save relevant pages, and persist crawler state."""
    raw_pages_path = Path(raw_pages_path)
    frontier_path = Path(frontier_path)
    visited_path = Path(visited_path)
    log_path = Path(log_path)
    if fresh and log_path.exists():
        # Keep crawl.log consistent with the other state files that --fresh discards.
        log_path.unlink()
    _setup_logging(log_path)
    start_time = time.time()

    domain_next_time: dict[str, float] = {}
    if fresh:
        # Ignore anything on disk and start over from just the seeds.
        pages = []
        frontier_high = load_seed_urls(seeds_path)
        frontier_low = []
        visited_entries = []
        logger.info("--fresh: discarding any existing raw_pages.json/frontier.json/visited.json and starting from seeds")
    else:
        # Load any previously saved pages, and resume both frontiers from disk (or from 0).
        # Both queues live in one frontier.json: {"frontier_high": {domain: [urls...]},
        # "frontier_low": {domain: [urls...]}, "domain_next_time": {domain: timestamp}}.
        raw = read_json(raw_pages_path, {"pages": []})
        pages = raw.get("pages", [])
        frontier_data = read_json(frontier_path, {"frontier_high": {}, "frontier_low": {}, "domain_next_time": {}})
        frontier_high = _flatten_frontier(frontier_data.get("frontier_high", {}))
        frontier_low = _flatten_frontier(frontier_data.get("frontier_low", {}))
        domain_next_time = frontier_data.get("domain_next_time", {}) or {}
        if not frontier_high and not frontier_low:
            # Fresh start: seed URLs are presumed Tuebingen-relevant, so they go straight into frontier_high.
            frontier_high = load_seed_urls(seeds_path)

        # Load which URLs were already visited, so we don't refetch them across runs
        visited_data = read_json(visited_path, {"visited": []})
        visited_entries = visited_data.get("visited", [])

    # Read the running summary.
    summary_path = raw_pages_path.parent / "crawl_summary.json"
    zeroed_totals = {"runs_completed": 0, "attempted_urls_total": 0, "elapsed_seconds_total": 0.0, "interrupted_runs": 0}
    prior = zeroed_totals if fresh else read_json(summary_path, zeroed_totals)

    state = CrawlerState(
        frontier_high=frontier_high,
        frontier_low=frontier_low,
        pages=pages,
        visited_entries=visited_entries,
        max_pages=max_pages,
        polite_delay=polite_delay,
        checkpoint_every=checkpoint_every,
        domain_next_time=domain_next_time,
        elapsed_offset=prior.get("elapsed_seconds_total", 0.0),
    )

    logger.info(
        f"starting crawl: {len(frontier_high)} urls in frontier_high, {len(frontier_low)} urls in frontier_low, "
        f"{len(pages)} pages already saved, max_pages={max_pages}, workers={workers}, fresh={fresh}"
    )

    checkpoint_paths = (raw_pages_path, frontier_path, visited_path)
    threads = [
        threading.Thread(
            target=_worker_loop,
            args=(state, i, timeout, checkpoint_paths),
            name=f"crawler-worker-{i}",
            daemon=True,
        )
        for i in range(max(1, workers))
    ]

    for t in threads:
        t.start()

    try:
        # Poll instead of a plain join() so Ctrl-C reaches the main thread promptly even though the actual work is happening on daemon worker threads.
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.2)
    except KeyboardInterrupt:
        state.interrupted = True
        state.stop_event.set()
        logger.info("interrupted by user (Ctrl-C) - waiting for in-flight requests to finish, then saving progress so the crawl can be resumed later")
        join_deadline = timeout + 2
        for t in threads:
            t.join(timeout=join_deadline)
            if t.is_alive():
                # This worker is stuck past its request timeout
                logger.info(f"warning: {t.name} did not finish within {join_deadline:.0f}s, abandoning it (it may still be running in the background)")

    pages, frontier_high, frontier_low, domain_next_time, visited_entries = state.snapshot_for_checkpoint()

    # Persist crawler state to disk so subsequent runs can resume where this one left off.
    with state.checkpoint_lock:
        _save_state(
            raw_pages_path,
            frontier_path,
            visited_path,
            pages,
            frontier_high,
            frontier_low,
            domain_next_time,
            visited_entries,
        )
    elapsed_seconds = time.time() - start_time

    # Merge this run's counts into the running totals so far (prior was read further up.
    summary = {
        "step": "crawling",
        "runs_completed": prior.get("runs_completed", 0) + 1,
        "total_pages": len(pages),
        "attempted_urls_this_run": state.attempted,
        "attempted_urls_total": prior.get("attempted_urls_total", 0) + state.attempted,
        "frontier_high_size": _frontier_total(frontier_high),
        "frontier_low_size": _frontier_total(frontier_low),
        "visited_size": len(visited_entries),
        "timeout": timeout,
        "polite_delay": polite_delay,
        "workers": workers,
        "last_run_interrupted": state.interrupted,
        "interrupted_runs": prior.get("interrupted_runs", 0) + (1 if state.interrupted else 0),
        "elapsed_seconds_this_run": round(elapsed_seconds, 2),
        "elapsed_human_this_run": _format_elapsed(elapsed_seconds),
        "elapsed_seconds_total": round(prior.get("elapsed_seconds_total", 0.0) + elapsed_seconds, 2),
        "elapsed_human_total": _format_elapsed(prior.get("elapsed_seconds_total", 0.0) + elapsed_seconds),
        "started_fresh_last_run": fresh,
        "last_updated": now_utc_iso(),
    }
    write_json(summary_path, summary)
    status = "interrupted" if state.interrupted else "done"
    logger.info(
        f"{status}: saved={state.saved} attempted={state.attempted} "
        f"frontier_high_remaining={_frontier_total(frontier_high)} frontier_low_remaining={_frontier_total(frontier_low)} "
        f"elapsed={summary['elapsed_human_this_run']}"
    )
    return summary
