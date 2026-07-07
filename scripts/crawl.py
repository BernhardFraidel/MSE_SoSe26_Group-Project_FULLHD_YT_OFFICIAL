from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import crawl


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl English Tuebingen-related web pages.")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--polite-delay",
        type=float,
        default=0.6,
        help="Minimum seconds between two requests to the same domain (subdomains share one timer). Not a global delay - different domains are fetched concurrently.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of concurrent worker threads fetching pages. Each domain is still only fetched at most once per --polite-delay seconds, regardless of worker count.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=5,
        help="Write frontier/visited/pages to disk every N attempted fetches, so an interrupted run (Ctrl-C) can be resumed later instead of restarting.",
    )
    args = parser.parse_args()
    summary = crawl(
        max_pages=args.max_pages,
        timeout=args.timeout,
        polite_delay=args.polite_delay,
        checkpoint_every=args.checkpoint_every,
        workers=args.workers,
    )
    print(summary)


if __name__ == "__main__":
    main()
