from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

# File extensions that are almost never HTML pages, used to skip obvious binary/asset links
NON_HTML_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".exe", ".dmg",
    ".zip", ".rar", ".tar", ".gz", ".7z", ".jpg", ".jpeg", ".png", ".gif",
    ".bmp", ".webp", ".svg", ".ico", ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".css", ".js", ".json", ".xml", ".rss", ".woff", ".woff2", ".ttf", ".eot"
)

# A short list of common two-part public suffixes. Not exhaustive (a real public-suffix
# list has thousands of entries), but enough to stop us from collapsing e.g. "co.uk"
# itself into a fake "registrable domain" of "uk".
TWO_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk",
    "co.jp", "ne.jp", "or.jp",
    "com.au", "net.au", "org.au",
    "co.nz", "co.za", "co.in", "co.at", "or.at",
    "com.br", "com.mx", "com.tr", "com.cn",
}

# Project root, one level up from this file (src/utils.py -> project root)
ROOT = Path(__file__).resolve().parent.parent


def project_path(*parts: str) -> Path:
    """Build an absolute path rooted at the project directory from the given path parts."""
    return ROOT.joinpath(*parts)


def now_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_url(url: str, base: str | None = None) -> str:
    """Resolve a (possibly relative) URL against an optional base and strip its fragment."""
    # Turn relative links (e.g. "/en/page") into absolute URLs using the base page
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url)
    # Force https so "http://x.de/x" and "https://x.de/x" normalize to the same URL and aren't treated as two different frontier/visited entries
    scheme = "https" if parsed.scheme in ("http", "https", "") else parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    # Drop the fragment (#...) since it never changes what the server returns
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def get_domain(url: str) -> str:
    """Return the "registrable domain" for a URL, used as the key for per-domain politeness. Subdomains collapse onto the same key so that, e.g., "en.wikipedia.org" and "de.wikipedia.org" share a single polite-delay timer"""
    host = urlparse(url).netloc.lower().split(":")[0]
    if not host:
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in TWO_PART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def is_probably_html_url(url: str) -> bool:
    """Check whether a URL looks like it points to an HTML page rather than a binary asset."""
    # Reject anything that isn't a regular web link (mailto:, javascript:, ftp:, etc. are caught upstream too)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    # Reject known binary/asset file extensions (images, docs, archives, ...)
    path = parsed.path.lower()
    return not path.endswith(NON_HTML_EXTENSIONS)


def short_snippet(text: str, length: int = 200) -> str:
    """Truncate text to roughly the given length, cutting on a word boundary."""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + "..."


def read_json(path: str | Path, default: object) -> object:
    """Read a JSON file, returning the given default if it's missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str | Path, data: object) -> None:
    """Write data as pretty-printed JSON to disk, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
