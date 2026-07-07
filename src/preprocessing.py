from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from src.utils import read_json, short_snippet, write_json


FALLBACK_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
    "you",
    "your",
    "we",
    "our",
    "can",
    "will",
    "not",
    "more",
    "about",
    "into",
    "also",
    "their",
    "they",
}


def _load_stopwords() -> set[str]:
    # Try to load NLTK's English stopword list, falling back to a hardcoded set if unavailable.
    try:
        from nltk.corpus import stopwords

        return set(stopwords.words("english")) | FALLBACK_STOPWORDS
    except Exception:
        return FALLBACK_STOPWORDS


def _load_stemmer():
    # Try to load NLTK's Porter stemmer, returning None if it can't be loaded.
    try:
        from nltk.stem import PorterStemmer

        return PorterStemmer()
    except Exception:
        return None


STOPWORDS = _load_stopwords()
STEMMER = _load_stemmer()


def normalize_tuebingen(text: str) -> str:
    # Normalize all spelling variants of "Tübingen"/"Tuebingen" to a single lowercase form.
    text = text.replace("TÜBINGEN", "tubingen").replace("Tübingen", "tubingen")
    text = text.replace("tübingen", "tubingen")
    text = re.sub(r"\btuebingen\b", "tubingen", text, flags=re.IGNORECASE)
    text = re.sub(r"\btubingen\b", "tubingen", text, flags=re.IGNORECASE)
    return text


def preprocess(text: str, use_stemming: bool = True) -> list[str]:
    # Lowercase, normalize, tokenize, strip stopwords/short tokens, and optionally stem the text.
    text = normalize_tuebingen(text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    cleaned: list[str] = []
    for token in tokens:
        if token in STOPWORDS or len(token) <= 1:
            continue
        if use_stemming and STEMMER is not None and token != "tubingen":
            token = STEMMER.stem(token)
        cleaned.append(token)
    return cleaned


def tokens_from_list(values: Iterable[str]) -> list[str]:
    # Join a list of strings into one text blob and preprocess it into tokens.
    return preprocess(" ".join(values))


def create_preprocessed_pages(raw_pages_path: str | Path, output_path: str | Path) -> dict:
    # Load raw page data, preprocess each page's text fields, and write the results to JSON.
    raw = read_json(raw_pages_path, {"pages": []})
    documents = []
    for page in raw.get("pages", []):
        title_tokens = preprocess(page.get("title", ""))
        heading_tokens = tokens_from_list(page.get("headings", []))
        body_tokens = preprocess(page.get("body", ""))
        documents.append(
            {
                "doc_id": page.get("doc_id"),
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "title_tokens": title_tokens,
                "heading_tokens": heading_tokens,
                "body_tokens_preview": body_tokens[:80],
                "body_length": len(body_tokens),
                "snippet": short_snippet(page.get("body", "")),
            }
        )
    output = {"documents": documents}
    write_json(output_path, output)
    return output
