import json
from collections import Counter
from pathlib import Path


def load_preprocessed_documents(path: str = "data/preprocessed_pages.json") -> list[dict]:
    """Load preprocessed documents from a JSON file.

    The indexer expects the preprocessing output format:
    {"documents": [...]}.
    """
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Unsupported preprocessed data in {input_path}: top-level JSON must be an object.")

    if "documents" not in data:
        raise ValueError(f"Unsupported preprocessed data in {input_path}: missing 'documents' field.")

    documents = data["documents"]
    if not isinstance(documents, list):
        raise ValueError(f"Unsupported preprocessed data in {input_path}: 'documents' must be a list.")

    return documents


def build_document_metadata(documents: list[dict]) -> list[dict]:
    """Create compact document metadata entries for the index."""
    metadata: list[dict] = []

    for document in documents:
        doc_id = document.get("doc_id")
        if doc_id is None:
            raise ValueError("Cannot build document metadata: document is missing 'doc_id'.")

        body_tokens = document.get("body_tokens", [])
        doc_length = document.get("body_length", len(body_tokens))

        metadata.append(
            {
                "doc_id": doc_id,
                "url": document.get("url", ""),
                "fetched_url": document.get("fetched_url", ""),
                "canonical_url": document.get("canonical_url", ""),
                "title": document.get("title", ""),
                "snippet": document.get("snippet", ""),
                "doc_length": doc_length,
                "outgoing_links": document.get("outgoing_links", []),
                "crawl_time": document.get("crawl_time", ""),
            }
        )

    return metadata


def build_inverted_index(documents: list[dict]) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Build an inverted index and document frequencies from preprocessed documents."""
    postings_by_term: dict[str, list[dict]] = {}
    document_frequencies: dict[str, int] = {}

    for document in documents:
        doc_id = document.get("doc_id")
        if doc_id is None:
            continue

        tokens = (
            document.get("title_tokens", [])
            + document.get("heading_tokens", [])
            + document.get("body_tokens", [])
        )
        if not tokens:
            continue

        term_counts = Counter(tokens)
        for term, frequency in term_counts.items():
            postings_by_term.setdefault(term, []).append({"doc_id": doc_id, "tf": frequency})
            document_frequencies[term] = document_frequencies.get(term, 0) + 1

    inverted_index = {
        term: sorted(postings, key=lambda posting: posting["doc_id"])
        for term, postings in sorted(postings_by_term.items())
    }
    sorted_document_frequencies = dict(sorted(document_frequencies.items()))

    return inverted_index, sorted_document_frequencies


def build_field_lengths(documents: list[dict]) -> dict[str, dict[str, int]]:
    """Build per-field token length mappings keyed by document id."""
    field_lengths = {
        "body": {},
        "title": {},
        "heading": {},
    }

    for document in documents:
        doc_id = document.get("doc_id")
        if doc_id is None:
            continue

        body_tokens = document.get("body_tokens", [])
        doc_id_key = str(doc_id)

        field_lengths["body"][doc_id_key] = document.get("body_length", len(body_tokens))
        field_lengths["title"][doc_id_key] = len(document.get("title_tokens", []))
        field_lengths["heading"][doc_id_key] = len(document.get("heading_tokens", []))

    return field_lengths


def compute_average_document_length(documents: list[dict]) -> float:
    """Compute the average body length across all documents."""
    if not documents:
        return 0.0

    total_length = 0
    for document in documents:
        body_tokens = document.get("body_tokens", [])
        total_length += document.get("body_length", len(body_tokens))

    return total_length / len(documents)
