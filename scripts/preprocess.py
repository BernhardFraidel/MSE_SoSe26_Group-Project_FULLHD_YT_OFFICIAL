from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.preprocessing import preprocess_raw_pages


def main() -> None:
    """Run preprocessing from the command line."""
    parser = argparse.ArgumentParser(description="Preprocess crawled Tuebingen web pages.")
    parser.add_argument("--input", default="data/raw_pages.json", help="Path to raw pages JSON.")
    parser.add_argument("--output", default="data/preprocessed_pages.json", help="Path for preprocessed documents JSON.")
    parser.add_argument(
        "--summary-output",
        default="data/preprocessing_summary.json",
        help="Path for preprocessing summary JSON.",
    )
    parser.add_argument("--no-stemming", action="store_true", help="Disable Porter stemming.")
    args = parser.parse_args()

    result = preprocess_raw_pages(
        input_path=args.input,
        output_path=args.output,
        summary_output_path=args.summary_output,
        use_stemming=not args.no_stemming,
    )
    summary = result["summary"]

    print("[preprocess] done")
    print(f"  documents: {summary['num_documents']}")
    print(f"  average body length: {summary['average_body_length']:.2f}")
    print(f"  vocabulary size: {summary['vocabulary_size']}")
    print(f"  output: {summary['output_path']}")
    print(f"  summary output: {summary['summary_output_path']}")


if __name__ == "__main__":
    main()
