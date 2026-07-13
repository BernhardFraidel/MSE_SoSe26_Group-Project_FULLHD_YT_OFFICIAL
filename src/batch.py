import csv
import json
import time
from pathlib import Path

from src.reranking import rerank
from src.retrieval import retrieve


def load_index(path: str = "data/index.json") -> dict:
    """Load the JSON search index used for batch retrieval."""
    index_path = Path(path)
    with index_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_batch_queries(path: str = "queries.tsv") -> list[dict]:
    """Load batch queries from tab-separated text file.

    Each line is expected to contain:
    query_id <TAB> query text.
    """
    input_path = Path(path)
    queries: list[dict] = []

    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            if not row:
                continue

            query_id, query = row
            queries.append({"query_id": query_id.strip(), "query": query.strip()})

    return queries



