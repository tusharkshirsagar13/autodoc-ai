"""Vector storage (ChromaDB) and hybrid semantic + keyword retrieval.

Isolation: every query is filtered by revision id at the database level
(Chroma `where` clause) *and* re-checked in Python before results are
returned, so chunks from other documents can never leak into an answer.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field

from config.settings import Settings
from src.ai.embeddings import Embedder, get_embedder, tokenize
from src.utils.logging_config import get_logger

log = get_logger("retrieval")

_clients: dict[str, object] = {}
_client_lock = threading.Lock()


def _collection_name(embedder: Embedder) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", embedder.key.lower()).strip("-")
    return f"autodoc-{slug}"[:60].rstrip("-")


def get_chroma_client(settings: Settings):
    path = str(settings.chroma_dir)
    with _client_lock:
        if path not in _clients:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            _clients[path] = chromadb.PersistentClient(
                path=path, settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False)
            )
        return _clients[path]


def reset_clients() -> None:
    with _client_lock:
        _clients.clear()


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict
    semantic_score: float = 0.0
    keyword_score: float = 0.0
    score: float = 0.0

    @property
    def page_number(self) -> int:
        return int(self.metadata.get("page_number", 0))

    @property
    def section(self) -> str:
        return self.metadata.get("section_heading", "")

    @property
    def revision_id(self) -> str:
        return self.metadata.get("revision_id", "")


class VectorStore:
    """Thin wrapper around one Chroma collection per embedding model."""

    def __init__(self, settings: Settings, embedder: Embedder | None = None):
        self.settings = settings
        self.embedder = embedder or get_embedder(settings)
        self.client = get_chroma_client(settings)
        self.collection = self.client.get_or_create_collection(
            name=_collection_name(self.embedder),
            metadata={"hnsw:space": "cosine", "embedding_key": self.embedder.key},
            embedding_function=None,
        )

    # ------------------------------------------------------------- writing
    def delete_revision(self, revision_id: str) -> None:
        self.collection.delete(where={"revision_id": revision_id})

    def add_chunks(self, ids: list[str], texts: list[str], embed_texts: list[str], metadatas: list[dict],
                   batch_size: int = 64, progress=None) -> None:
        total = len(ids)
        for start in range(0, total, batch_size):
            end = start + batch_size
            vectors = self.embedder.embed_documents(embed_texts[start:end])
            self.collection.upsert(ids=ids[start:end], documents=texts[start:end], embeddings=vectors,
                                   metadatas=metadatas[start:end])
            if progress:
                progress(min(end, total) / max(total, 1), f"Embedded {min(end, total)} of {total} chunks")

    # ------------------------------------------------------------- reading
    def count_for_revision(self, revision_id: str) -> int:
        result = self.collection.get(where={"revision_id": revision_id}, include=[])
        return len(result.get("ids", []))

    def get_revision_chunks(self, revision_id: str) -> list[RetrievedChunk]:
        result = self.collection.get(where={"revision_id": revision_id}, include=["documents", "metadatas"])
        chunks = [RetrievedChunk(chunk_id=i, text=d or "", metadata=m or {})
                  for i, d, m in zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", []))]
        chunks.sort(key=lambda c: int(c.metadata.get("chunk_index", 0)))
        return chunks

    def semantic_search(self, query: str, revision_ids: list[str], n_results: int) -> list[RetrievedChunk]:
        if not revision_ids:
            return []
        where = {"revision_id": revision_ids[0]} if len(revision_ids) == 1 else {"revision_id": {"$in": revision_ids}}
        available = sum(self.count_for_revision(r) for r in revision_ids)
        if available == 0:
            return []
        vector = self.embedder.embed_query(query)
        result = self.collection.query(query_embeddings=[vector], n_results=min(n_results, available), where=where,
                                       include=["documents", "metadatas", "distances"])
        out = []
        for cid, doc, meta, dist in zip(result["ids"][0], result["documents"][0], result["metadatas"][0],
                                        result["distances"][0]):
            similarity = max(0.0, 1.0 - float(dist))  # cosine distance -> similarity
            out.append(RetrievedChunk(chunk_id=cid, text=doc or "", metadata=meta or {}, semantic_score=similarity))
        return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.lengths = [len(d) for d in docs]
        self.avgdl = (sum(self.lengths) / len(docs)) if docs else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = []
        for tf, length in zip(self.tf, self.lengths):
            score = 0.0
            for token in set(query_tokens):
                if token not in tf:
                    continue
                freq = tf[token]
                denom = freq + self.k1 * (1 - self.b + self.b * length / (self.avgdl or 1))
                score += self.idf.get(token, 0.0) * freq * (self.k1 + 1) / denom
            out.append(score)
        return out


_STOPWORDS = set("""a an the of to in on for and or is are was were be by with from this that these those what which
who whom how does do did can could should would there their its as at it into about between within any all
document section describe described explain list show me give tell please""".split())


@dataclass
class Retriever:
    settings: Settings
    store: VectorStore | None = None
    _bm25_cache: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.store is None:
            self.store = VectorStore(self.settings)

    def _keyword_index(self, revision_id: str):
        chunks = self.store.get_revision_chunks(revision_id)
        cache_key = (revision_id, len(chunks), chunks[-1].chunk_id if chunks else "")
        cached = self._bm25_cache.get(revision_id)
        if cached and cached[0] == cache_key:
            return cached[1], cached[2]
        index = BM25([tokenize(c.metadata.get("section_heading", "") + " " + c.text) for c in chunks])
        self._bm25_cache[revision_id] = (cache_key, chunks, index)
        return chunks, index

    def search(self, query: str, revision_ids: list[str], top_k: int | None = None) -> list[RetrievedChunk]:
        top_k = top_k or self.settings.retrieval_top_k
        weight = self.settings.hybrid_keyword_weight
        candidates: dict[str, RetrievedChunk] = {}
        for chunk in self.store.semantic_search(query, revision_ids, n_results=max(top_k * 3, 10)):
            candidates[chunk.chunk_id] = chunk

        query_tokens = [t for t in tokenize(query) if t not in _STOPWORDS]
        if weight > 0 and query_tokens:
            for revision_id in revision_ids:
                chunks, index = self._keyword_index(revision_id)
                scores = index.scores(query_tokens)
                if not scores:
                    continue
                best = max(scores) or 1.0
                max_idf = max(index.idf.values(), default=1.0)
                unique_q = set(query_tokens)
                total_idf = sum(index.idf.get(t, max_idf) for t in unique_q) or 1.0
                ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[: max(top_k * 3, 10)]
                for i in ranked:
                    if scores[i] <= 0:
                        continue
                    # Relative BM25 rank scaled by how much of the query (idf-weighted) the chunk covers,
                    # so a single incidental word match cannot look like strong evidence.
                    coverage = sum(index.idf.get(t, 0.0) for t in unique_q if t in index.tf[i]) / total_idf
                    normalized = (scores[i] / best) * coverage
                    existing = candidates.get(chunks[i].chunk_id)
                    if existing:
                        existing.keyword_score = normalized
                    else:
                        c = chunks[i]
                        candidates[c.chunk_id] = RetrievedChunk(chunk_id=c.chunk_id, text=c.text, metadata=c.metadata,
                                                                keyword_score=normalized)
        allowed = set(revision_ids)
        results = [c for c in candidates.values() if c.revision_id in allowed]  # isolation re-check
        for c in results:
            c.score = (1 - weight) * c.semantic_score + weight * c.keyword_score
        results.sort(key=lambda c: c.score, reverse=True)
        return results[:top_k]

    def search_text(self, phrase: str, revision_id: str) -> list[RetrievedChunk]:
        """Exact, case-insensitive phrase search (used for 'which sections mention X')."""
        chunks, _ = self._keyword_index(revision_id)
        needle = phrase.lower()
        return [c for c in chunks if needle in c.text.lower()]
