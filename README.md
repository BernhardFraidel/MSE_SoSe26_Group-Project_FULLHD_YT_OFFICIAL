# Tuebingen Search Engine

Initial project structure for a student-style search engine project in the course **Modern Search Engines**.

The project will crawl English web pages related to Tuebingen, store them locally, build an index, retrieve ranked results for queries, export batch results, and provide a small Streamlit interface.

At this initial stage, the files are placeholders. Data exchange is planned as JSON first, so there are no `.pkl` or `.tsv` files in this initial commit.

## Project Structure

```text
mse-tuebingen-search/
|-- README.md
|-- requirements.txt
|-- seeds.json
|-- queries.json
|-- data/
|   |-- raw_pages.json
|   |-- index.json
|   |-- frontier.json
|   |-- visited.json
|   |-- crawl_summary.json
|   |-- preprocessed_pages.json
|   |-- index_summary.json
|   |-- results.json
|   `-- batch_summary.json
|-- src/
|   |-- crawler.py
|   |-- preprocessing.py
|   |-- text_representations.py
|   |-- indexer.py
|   |-- retrieval.py
|   |-- reranking.py
|   |-- batch.py
|   `-- utils.py
|-- frontend/
|   `-- app.py
`-- scripts/
    |-- crawl.py
    |-- build_index.py
    |-- run_batch.py
    `-- smoke_test.py
```

## File Responsibilities

- `seeds.json`: start URLs for the crawler.
- `queries.json`: batch queries.
- `data/raw_pages.json`: locally stored crawled pages.
- `data/frontier.json`: simple JSON list of URLs still waiting to be crawled.
- `data/visited.json`: URLs already visited.
- `data/index.json`: readable index representation for the initial project stage.
- `data/crawl_summary.json`: crawl statistics.
- `data/preprocessed_pages.json`: token preview after preprocessing.
- `data/index_summary.json`: index statistics.
- `data/results.json`: batch retrieval results.
- `data/batch_summary.json`: compact batch retrieval summary.
- `src/crawler.py`: crawling, URL filtering, page extraction.
- `src/preprocessing.py`: tokenization, normalization, stopword removal, optional stemming.
- `src/text_representations.py`: term frequency, document frequency, IDF helpers.
- `src/indexer.py`: inverted index construction.
- `src/retrieval.py`: BM25 retrieval.
- `src/reranking.py`: second-stage ranking signals.
- `src/batch.py`: batch retrieval export.
- `src/utils.py`: shared helper functions.
- `frontend/app.py`: Streamlit user interface.
- `scripts/`: simple command-line entry points.

## Pipeline Stages

### 1. Seed URLs

- Input: manually selected Tuebingen-related start URLs
- Processing: read seed URLs and normalize them
- Output: initial crawl frontier
- Files: `seeds.json`, `src/crawler.py`

Example JSON:

```json
{
  "seeds": [
    {
      "url": "https://uni-tuebingen.de/en/",
      "label": "University of Tuebingen",
      "priority": 1
    }
  ]
}
```

### 2. Crawling

- Input: `seeds.json`, `data/frontier.json`, `data/visited.json`
- Processing: fetch HTML pages, respect robots.txt, extract title/body/headings/links, filter English Tuebingen-related pages
- Output: `data/raw_pages.json`, updated `data/frontier.json`, updated `data/visited.json`, `data/crawl_summary.json`
- Files: `src/crawler.py`, `scripts/crawl.py`, `src/utils.py`

Example JSON for `data/raw_pages.json`:

```json
{
  "pages": [
    {
      "doc_id": 0,
      "url": "https://uni-tuebingen.de/en/",
      "fetched_url": "https://uni-tuebingen.de/en/",
      "canonical_url": "https://uni-tuebingen.de/en",
      "title": "Home | University of Tuebingen",
      "headings": ["University of Tuebingen"],
      "body": "Example page text...",
      "outgoing_links": ["https://uni-tuebingen.de/en/study"],
      "language": "en",
      "is_tuebingen_related": true,
      "crawl_time": "2026-07-05T12:00:00Z"
    }
  ]
}
```

Example JSON for `data/frontier.json`:

```json
[
  "https://www.unimuseum.uni-tuebingen.de/de/ausstellungen/sonderausstellungen/agora/die-agora-von-athen",
  "https://www.unimuseum.uni-tuebingen.de/de/ausstellungen/sonderausstellungen/agora-1",
  "https://www.unimuseum.uni-tuebingen.de/de/ausstellungen/sonderausstellungen/dirty-science"
]
```

Example JSON for `data/visited.json`:

```json
{
  "visited": [
    {
      "url": "https://uni-tuebingen.de/en/",
      "visited_at": "2026-07-05T12:00:00Z",
      "status_code": 200
    }
  ]
}
```

Example JSON for `data/crawl_summary.json`:

```json
{
  "step": "crawling",
  "saved_pages": 1,
  "attempted_urls": 1,
  "frontier_size": 1,
  "visited_size": 1,
  "request_timeout_seconds": 6,
  "polite_delay_seconds": 0.6
}
```

### 3. Preprocessing

- Input: raw page text from `data/raw_pages.json`
- Processing: lowercase, tokenize, remove punctuation, normalize Tuebingen spelling variants, remove stopwords, apply optional stemming
- Output: token preview in `data/preprocessed_pages.json`
- Files: `src/preprocessing.py`

Example JSON:

```json
{
  "documents": [
    {
      "doc_id": 0,
      "url": "https://uni-tuebingen.de/en/",
      "title": "Home | University of Tuebingen",
      "title_tokens": ["home", "univers", "tubingen"],
      "heading_tokens": ["univers", "tubingen"],
      "body_tokens_preview": ["student", "research", "campus"],
      "body_length": 320
    }
  ]
}
```

### 4. Indexing

- Input: preprocessed document tokens
- Processing: build manual inverted index with term frequencies, document lengths, metadata, and link graph
- Output: `data/index.json`, `data/index_summary.json`
- Files: `src/indexer.py`, `src/text_representations.py`, `scripts/build_index.py`

Example JSON for `data/index.json`:

```json
{
  "documents": [
    {
      "doc_id": 0,
      "url": "https://uni-tuebingen.de/en/",
      "title": "Home | University of Tuebingen",
      "snippet": "Example snippet...",
      "doc_length": 320,
      "outgoing_links": ["https://uni-tuebingen.de/en/study"]
    }
  ],
  "inverted_index": {
    "tubingen": [
      {
        "doc_id": 0,
        "tf": 4
      }
    ]
  },
  "document_frequencies": {
    "tubingen": 1
  },
  "field_lengths": {
    "body": {
      "0": 320
    },
    "title": {
      "0": 3
    }
  },
  "average_document_length": 320.0,
  "link_graph": {
    "0": []
  }
}
```

Example JSON for `data/index_summary.json`:

```json
{
  "step": "indexing",
  "num_docs": 1,
  "vocabulary_size": 1,
  "average_document_length": 320.0,
  "documents_with_outgoing_links": 1,
  "lsa_available": false
}
```

### 5. Retrieval / BM25

- Input: query from `queries.json` or the UI, plus `data/index.json`
- Processing: preprocess query, score documents with manually implemented BM25, retrieve top candidates and attach document metadata such as title, URL, and snippet
- Output: first-stage ranked result list
- Files: `src/retrieval.py`, `src/preprocessing.py`

Example JSON:

```json
{
  "query_id": "1",
  "query": "tuebingen attractions",
  "query_tokens": ["tubingen", "attract"],
  "candidates": [
    {
      "doc_id": 0,
      "url": "https://uni-tuebingen.de/en/",
      "title": "Home | University of Tuebingen",
      "snippet": "Example snippet...",
      "bm25_score": 2.41,
      "matched_terms": ["tubingen"]
    }
  ]
}
```

### 6. Re-Ranking

- Input: BM25 candidate results
- Processing: add field boosts, pseudo relevance feedback bonus, link score bonus, optional semantic LSA/SVD score
- Output: final ranked result list
- Files: `src/reranking.py`, `src/retrieval.py`

Example JSON:

```json
{
  "query_id": "1",
  "query": "tuebingen attractions",
  "expansion_terms": ["museum", "castle"],
  "results": [
    {
      "rank": 1,
      "doc_id": 0,
      "url": "https://uni-tuebingen.de/en/",
      "title": "Home | University of Tuebingen",
      "snippet": "Example snippet...",
      "score": 0.84,
      "score_details": {
        "normalized_bm25": 0.7,
        "normalized_field_boost": 0.1,
        "normalized_prf": 0.02,
        "normalized_link": 0.01,
        "normalized_lsa": 0.01
      }
    }
  ]
}
```

### 7. Batch Output

- Input: `queries.json`, `data/index.json`
- Processing: run retrieval for each query and keep up to 100 results
- Output: `data/results.json`, `data/batch_summary.json`
- Files: `src/batch.py`, `scripts/run_batch.py`

Example JSON for `queries.json`:

```json
{
  "queries": [
    {
      "query_id": "1",
      "text": "tuebingen attractions"
    }
  ]
}
```

Example JSON for `data/results.json`:

```json
{
  "queries": [
    {
      "query_id": "1",
      "query": "tuebingen attractions",
      "num_results": 1,
      "runtime_seconds": 0.05,
      "results": [
        {
          "rank": 1,
          "url": "https://uni-tuebingen.de/en/",
          "title": "Home | University of Tuebingen",
          "snippet": "Example snippet...",
          "score": 0.84,
          "score_details": {
            "bm25_component": 0.7,
            "field_component": 0.1,
            "prf_component": 0.02,
            "link_component": 0.01,
            "lsa_component": 0.01
          }
        }
      ]
    }
  ]
}
```

Example JSON for `data/batch_summary.json`:

```json
{
  "step": "batch_retrieval_summary",
  "query_count": 1,
  "total_results": 1,
  "average_runtime_seconds": 0.05
}
```

### 8. Streamlit UI

- Input: interactive user query in the browser
- Processing: run retrieval, show result cards, score breakdown, highlighted query terms, and filters
- Output: interactive search interface
- Files: `frontend/app.py`, `src/retrieval.py`

Example JSON for an internal UI result object:

```json
{
  "query": "tuebingen attractions",
  "search_time_seconds": 0.05,
  "indexed_pages": 1,
  "selected_category": "Attractions",
  "result_cards": [
    {
      "rank": 1,
      "title": "Home | University of Tuebingen",
      "url": "https://uni-tuebingen.de/en/",
      "snippet": "Example snippet...",
      "highlighted_terms": ["tubingen", "attract"],
      "category": "Attractions",
      "score": 0.84,
      "why_this_result": "Matched query terms in body and title."
    }
  ]
}
```
