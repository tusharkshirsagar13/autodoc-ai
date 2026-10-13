"""Embedding backends.

* `sentence-transformers` (default): a semantic model such as
  BAAI/bge-small-en-v1.5 (~130 MB, CPU friendly). Downloaded once from the
  Hugging Face Hub and cached locally; loaded lazily on first use.
* `hashing`: a deterministic, offline lexical embedder (feature hashing of
  word uni/bi-grams and identifier sub-tokens). It needs no download and no
  network, but it only captures word overlap, not meaning. It is intended for
  air-gapped demos and automated tests, and is selected explicitly — the app
  never switches to it silently.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from abc import ABC, abstractmethod

from config.settings import Settings
from src.utils.errors import EmbeddingError
from src.utils.logging_config import get_logger

log = get_logger("embeddings")

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """Tokenizer shared by the hashing embedder and BM25 keyword search.

    Identifiers like `Sig_VehicleSpeed` yield the full token plus sub-tokens
    (`sig`, `vehicle`, `speed`) so both exact and partial matches work.
    """
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        low = raw.lower()
        tokens.append(low)
        parts = [p.lower() for piece in raw.split("_") for p in _CAMEL.findall(piece)]
        if len(parts) > 1:
            tokens.extend(p for p in parts if len(p) > 1)
    return tokens


class Embedder(ABC):
    backend: str
    model_name: str
    dimension: int | None = None

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.model_name}"

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder(Embedder):
    backend = "hashing"

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.model_name = f"hashing-{dimension}"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = tokenize(text)
        features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
        counts: dict[str, int] = {}
        for f in features:
            counts[f] = counts.get(f, 0) + 1
        for feature, count in counts.items():
            digest = hashlib.md5(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class SentenceTransformerEmbedder(Embedder):
    backend = "sentence-transformers"

    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingError(
                        "The sentence-transformers package is not installed.",
                        "Run `pip install -r requirements.txt`, or set EMBEDDING_BACKEND=hashing for offline mode.",
                    ) from exc
                try:
                    log.info("Loading embedding model %s", self.model_name)
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                    dim_fn = getattr(self._model, "get_embedding_dimension", None) or \
                        getattr(self._model, "get_sentence_embedding_dimension")
                    self.dimension = dim_fn()
                except Exception as exc:
                    raise EmbeddingError(
                        f"The embedding model '{self.model_name}' could not be loaded ({type(exc).__name__}).",
                        "The first run downloads the model from Hugging Face; check your internet connection, "
                        "or choose the offline 'hashing' backend on the Settings page.",
                    ) from exc
        return self._model

    def _query_prefix(self) -> str:
        name = self.model_name.lower()
        if "bge" in name and "-en" in name:
            return "Represent this sentence for searching relevant passages: "
        if "e5" in name:
            return "query: "
        return ""

    def _doc_prefix(self) -> str:
        return "passage: " if "e5" in self.model_name.lower() else ""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        prefix = self._doc_prefix()
        vectors = model.encode([prefix + t for t in texts], batch_size=32, normalize_embeddings=True,
                               show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        vector = model.encode([self._query_prefix() + text], normalize_embeddings=True, show_progress_bar=False)[0]
        return vector.tolist()


_cache: dict[str, Embedder] = {}
_cache_lock = threading.Lock()


def get_embedder(settings: Settings) -> Embedder:
    key = f"{settings.embedding_backend}:{settings.embedding_model}:{settings.embedding_device}"
    with _cache_lock:
        if key not in _cache:
            if settings.embedding_backend == "hashing":
                _cache[key] = HashingEmbedder()
            else:
                _cache[key] = SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_device)
        return _cache[key]


def embedder_key(settings: Settings) -> tuple[str, str]:
    """(backend, model_name) that will be recorded for revisions indexed with these settings."""
    if settings.embedding_backend == "hashing":
        return "hashing", HashingEmbedder().model_name
    return "sentence-transformers", settings.embedding_model


def embedding_status(settings: Settings) -> dict:
    backend, model = embedder_key(settings)
    if backend == "hashing":
        return {"backend": backend, "model": model, "installed": True, "loaded": True,
                "note": "Offline lexical embeddings — no download required."}
    try:
        import importlib.util
        installed = importlib.util.find_spec("sentence_transformers") is not None
    except Exception:
        installed = False
    embedder = _cache.get(f"{settings.embedding_backend}:{settings.embedding_model}:{settings.embedding_device}")
    loaded = bool(embedder and getattr(embedder, "_model", None) is not None)
    note = "" if installed else "sentence-transformers is not installed."
    return {"backend": backend, "model": model, "installed": installed, "loaded": loaded, "note": note}
