"""Central configuration for AutoDoc AI.

Configuration is resolved in this order (later wins):
  1. Safe built-in defaults (below)
  2. Environment variables / `.env` file
  3. Non-secret runtime overrides saved from the Settings page
     (`data/user_settings.json`)

Secrets (API keys) are ONLY ever read from the environment. They are never
written to disk by the application, never logged, and never shown in the UI.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

try:  # python-dotenv is optional at import time (tests may not need it)
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

APP_NAME = "AutoDoc AI"
APP_SUBTITLE = "Automotive Architecture Intelligence Platform"
APP_TAGLINE = "Turn complex automotive software documents into actionable engineering knowledge."
APP_VERSION = "1.0.0"

# Provider identifiers
LLM_PROVIDERS = {
    "none": "Not configured (retrieval-only mode)",
    "openai": "OpenAI or OpenAI-compatible API (official openai SDK)",
    "anthropic": "Anthropic Claude API (official anthropic SDK)",
    "ollama": "Ollama local model (runs on this machine)",
}
EMBEDDING_BACKENDS = {
    "sentence-transformers": "Sentence Transformers model (semantic, recommended)",
    "hashing": "Offline hashing embedder (lexical, no download, lower quality)",
}

# Fields that may be changed at runtime from the Settings page (never secrets).
RUNTIME_EDITABLE = {
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout_seconds",
    "embedding_backend",
    "embedding_model",
    "chunk_size",
    "chunk_overlap",
    "retrieval_top_k",
    "hybrid_keyword_weight",
    "min_relevance_score",
    "min_relevance_score_hashing",
    "ocr_enabled",
    "ocr_language",
}


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(_env("AUTODOC_DATA_DIR", str(PROJECT_ROOT / "data"))))
    upload_dir: Path = field(default_factory=lambda: Path(_env("AUTODOC_UPLOAD_DIR", str(PROJECT_ROOT / "uploads"))))
    chroma_dir: Path = field(default_factory=lambda: Path(_env("AUTODOC_CHROMA_DIR", str(PROJECT_ROOT / "chroma_db"))))
    database_file: str = field(default_factory=lambda: _env("AUTODOC_DATABASE_FILE", "autodoc.sqlite3"))

    # Upload safeguards
    max_upload_mb: int = field(default_factory=lambda: _env_int("AUTODOC_MAX_UPLOAD_MB", 50))
    max_documents: int = field(default_factory=lambda: _env_int("AUTODOC_MAX_DOCUMENTS", 100))
    max_pages: int = field(default_factory=lambda: _env_int("AUTODOC_MAX_PAGES", 1500))

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.1))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 1200))
    llm_timeout_seconds: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT_SECONDS", 60))
    ollama_host: str = field(default_factory=lambda: _env("OLLAMA_HOST", "http://localhost:11434"))

    # Embeddings / retrieval
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "sentence-transformers"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    embedding_device: str = field(default_factory=lambda: _env("EMBEDDING_DEVICE", "cpu"))
    chunk_size: int = field(default_factory=lambda: _env_int("CHUNK_SIZE", 900))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 150))
    retrieval_top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 6))
    hybrid_keyword_weight: float = field(default_factory=lambda: _env_float("HYBRID_KEYWORD_WEIGHT", 0.35))
    # Minimum hybrid relevance score for a passage to count as evidence. Semantic models give
    # unrelated text higher cosine similarity than the lexical hashing embedder, so each
    # backend has its own threshold. Tune with your own documents.
    min_relevance_score: float = field(default_factory=lambda: _env_float("MIN_RELEVANCE_SCORE", 0.35))
    min_relevance_score_hashing: float = field(default_factory=lambda: _env_float("MIN_RELEVANCE_SCORE_HASHING", 0.15))
    max_context_chars: int = field(default_factory=lambda: _env_int("MAX_CONTEXT_CHARS", 12000))

    # OCR
    ocr_enabled: bool = field(default_factory=lambda: _env_bool("OCR_ENABLED", True))
    ocr_language: str = field(default_factory=lambda: _env("OCR_LANGUAGE", "eng"))
    ocr_dpi: int = field(default_factory=lambda: _env_int("OCR_DPI", 200))
    tesseract_cmd: str = field(default_factory=lambda: _env("TESSERACT_CMD", ""))
    low_text_threshold: int = field(default_factory=lambda: _env_int("LOW_TEXT_THRESHOLD", 40))

    # Logging
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_file

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def user_settings_path(self) -> Path:
        return self.data_dir / "user_settings.json"

    @property
    def relevance_threshold(self) -> float:
        if self.embedding_backend == "hashing":
            return self.min_relevance_score_hashing
        return self.min_relevance_score

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # --- secrets: read on demand, never stored on the object ---------------
    @staticmethod
    def api_key_for(provider: str) -> str | None:
        env_name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider)
        if not env_name:
            return None
        value = os.getenv(env_name)
        return value or None

    @staticmethod
    def api_key_env_name(provider: str) -> str | None:
        return {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider)

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.upload_dir, self.chroma_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def apply_overrides(self, overrides: dict) -> None:
        valid = {f.name: f for f in fields(self)}
        for key, value in overrides.items():
            if key not in RUNTIME_EDITABLE or key not in valid:
                continue
            current = getattr(self, key)
            try:
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, int):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            setattr(self, key, value)
        self.validate()

    def validate(self) -> None:
        if self.llm_provider not in LLM_PROVIDERS:
            self.llm_provider = "none"
        if self.embedding_backend not in EMBEDDING_BACKENDS:
            self.embedding_backend = "sentence-transformers"
        self.chunk_size = max(200, min(self.chunk_size, 4000))
        self.chunk_overlap = max(0, min(self.chunk_overlap, self.chunk_size // 2))
        self.retrieval_top_k = max(1, min(self.retrieval_top_k, 20))
        self.hybrid_keyword_weight = max(0.0, min(self.hybrid_keyword_weight, 1.0))
        self.llm_temperature = max(0.0, min(self.llm_temperature, 1.0))
        self.llm_max_tokens = max(128, min(self.llm_max_tokens, 8000))
        self.llm_timeout_seconds = max(5, min(self.llm_timeout_seconds, 600))

    def save_overrides(self, overrides: dict) -> None:
        clean = {k: v for k, v in overrides.items() if k in RUNTIME_EDITABLE}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        existing = load_user_overrides(self)
        existing.update(clean)
        self.user_settings_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")
        self.apply_overrides(clean)

    def public_dict(self) -> dict:
        """Settings safe to display (contains no secrets)."""
        data = asdict(self)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in data.items()}


def load_user_overrides(settings: Settings) -> dict:
    path = settings.user_settings_path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        s = Settings()
        s.apply_overrides(load_user_overrides(s))
        s.validate()
        s.ensure_directories()
        _settings = s
    return _settings


def reset_settings_for_tests(settings: Settings) -> None:
    global _settings
    settings.validate()
    settings.ensure_directories()
    _settings = settings
