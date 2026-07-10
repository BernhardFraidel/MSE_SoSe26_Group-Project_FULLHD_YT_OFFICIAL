import json
import math
from collections import defaultdict
import nltk

try:
    from src.preprocessing import preprocess
except ImportError:
    def preprocess(text):
        # Einfacher Fallback
        return text.lower().split()

def compute_idf(N, df):
    """
    Berechnet den Okapi BM25 IDF-Wert.
    Das '+ 1.0' im Logarithmus verhindert negative IDF-Werte bei sehr häufigen Termen.
    """
    return math.log(1.0 + (N - df + 0.5) / (df + 0.5))

def correct_query_spelling(query_tokens: list[str], index_data: dict, max_distance: int = 2) -> list[str]:
    """
    Prüft, ob Query-Tokens Tippfehler enthalten.
    Fangt Fehler im Wortstamm (via Levenshtein) als auch 
    Tippfehler am Wortende (via Prefix-Matching) ab.
    """
    # Das bekannte Vokabular aus index.json laden (enthält die Stämme)
    vocabulary = index_data.get("document_frequencies", {}).keys()
    
    if not vocabulary:
        return query_tokens

    corrected_tokens = []
    
    for token in query_tokens:
        # Wenn das Wort exakt so im Index existiert, übernehmen wir es direkt
        if token in vocabulary or token == "tubingen" or token.isdigit():
            corrected_tokens.append(token)
            continue
            
        best_match = token
        min_dist = float('inf')
        found_via_prefix = False
        
        for vocab_term in vocabulary:
            # behandlung von suffix fehlern
            if token.startswith(vocab_term) and (len(token) - len(vocab_term) <= 4):
                best_match = vocab_term
                found_via_prefix = True
                break
                
            # levenshtein korrektur
            # Performance-Optimierung: Überspringe Wörter mit zu starkem Längenunterschied
            if abs(len(vocab_term) - len(token)) > max_distance:
                continue
                
            dist = nltk.edit_distance(token, vocab_term)
            
            if dist < min_dist and dist <= max_distance:
                min_dist = dist
                best_match = vocab_term
                
        corrected_tokens.append(best_match)
        
    return corrected_tokens

def retrieve(query, index, top_k=100, k1=1.2, b=0.75):
    """
    First-Stage-Retrieval mit dem BM25-Algorithmus.
    
    :param query: Der Suchstring des Nutzers (str)
    :param index: Pfad zur 'data/index.json' (str) oder bereits geladenes Dictionary
    :param top_k: Anzahl der zurückzugebenden Dokumente (Standard: 100)
    :param k1: BM25 Term-Sättigungsparameter (Standard: 1.2)
    :param b: BM25 Längennormalisierungsparameter (Standard: 0.75)
    :return: Ein Dictionary mit Query-Infos und den Top-100-Kandidaten
    """
    # Index laden
    if isinstance(index, str):
        with open(index, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
    else:
        index_data = index

    # Globale Index-Statistiken extrahieren
    documents = index_data.get("documents", [])
    inverted_index = index_data.get("inverted_index", {})
    doc_frequencies = index_data.get("document_frequencies", {})
    avgdl = index_data.get("average_document_length", 1.0)
    
    # Gesamtanzahl der Dokumente (N)
    N = len(documents)
    if N == 0:
        return {"query": query, "query_tokens": [], "candidates": []}

    # Lookup-Dictionary für Dokumentenlängen (|D|) und Metadaten
    doc_lengths = {}
    doc_metadata = {}
    for doc in documents:
        doc_id = doc["doc_id"]
        doc_lengths[doc_id] = doc.get("doc_length", 1)
        doc_metadata[doc_id] = doc

    # Query preprozessieren
    query_tokens = preprocess(query)
    query_tokens = correct_query_spelling(query_tokens, index_data)

    # BM25 Scoring (Term-at-a-Time Ansatz)
    scores = defaultdict(float)
    matched_terms_per_doc = defaultdict(list)

    for token in set(query_tokens):
        if token not in inverted_index:
            continue
            
        # IDF für den aktuellen Query-Term berechnen
        df = doc_frequencies.get(token, len(inverted_index[token]))
        idf = compute_idf(N, df)
        
        # Posting-Liste ablaufen
        for posting in inverted_index[token]:
            doc_id = posting["doc_id"]
            tf = posting["tf"]
            doc_len = doc_lengths.get(doc_id, avgdl)
            
            # BM25 Formel anwenden
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * (doc_len / avgdl))
            score_contribution = idf * (numerator / denominator)
            
            scores[doc_id] += score_contribution
            matched_terms_per_doc[doc_id].append(token)

    # Dokumente nach Score absteigend sortieren und Top-K auswählen
    ranked_doc_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)[:top_k]

    # Ergebnis im geforderten JSON-Format aufbereiten
    candidates = []
    for doc_id in ranked_doc_ids:
        meta = doc_metadata.get(doc_id, {})
        candidates.append({
            "doc_id": doc_id,
            "url": meta.get("url", ""),
            "title": meta.get("title", ""),
            "snippet": meta.get("snippet", ""),
            "bm25_score": round(scores[doc_id], 4),
            "matched_terms": matched_terms_per_doc[doc_id]
        })

    return {
        "query": query,
        "query_tokens": query_tokens,
        "candidates": candidates
    }