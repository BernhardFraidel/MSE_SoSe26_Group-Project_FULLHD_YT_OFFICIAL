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
