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
    parser.add_argument("--polite-delay", type=float, default=0.6)
    args = parser.parse_args()
    summary = crawl(max_pages=args.max_pages, timeout=args.timeout, polite_delay=args.polite_delay)
    print(summary)


if __name__ == "__main__":
    main()
