from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.preprocessing import create_preprocessed_pages, preprocess
from src.utils import read_json

RAW_PAGES_PATH = PROJECT_ROOT / "data" / "raw_pages.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "preprocessed_pages.json"


def main() -> None:
    # Mke sure the raw data file exists
    if not RAW_PAGES_PATH.exists():
        raise FileNotFoundError(f"Could not find raw pages file at: {RAW_PAGES_PATH}")

    raw = read_json(RAW_PAGES_PATH, {"pages": []})
    pages = raw.get("pages", [])
    print(f"Loaded {len(pages)} raw page(s) from {RAW_PAGES_PATH}")

    # Run the full preprocessing pipeline and write results out.
    result = create_preprocessed_pages(RAW_PAGES_PATH, OUTPUT_PATH)
    print(f"\nWrote {len(result['documents'])} preprocessed document(s) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()