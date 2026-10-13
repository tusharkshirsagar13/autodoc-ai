"""Shared fixtures. Tests run fully offline: the hashing embedder is used and
no LLM provider is contacted (providers are mocked where needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings, reset_settings_for_tests  # noqa: E402
from scripts.generate_sample_data import main as generate_samples  # noqa: E402


@pytest.fixture(scope="session")
def sample_pdfs(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("samples")
    rev_a, rev_b = generate_samples(out)
    return {"A": rev_a, "B": rev_b}


@pytest.fixture()
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", chroma_dir=tmp_path / "chroma")
    s.embedding_backend = "hashing"
    s.llm_provider = "none"
    s.llm_model = ""
    s.ocr_enabled = False
    reset_settings_for_tests(s)
    yield s
    from src.ai.retrieval import reset_clients
    from src.database.initialization import dispose_engines

    dispose_engines()
    reset_clients()


@pytest.fixture()
def processed(settings, sample_pdfs) -> dict:
    """Upload and process both sample revisions into an isolated store."""
    from src.services import document_service

    rev_a = document_service.upload_revision(settings, sample_pdfs["A"].name, sample_pdfs["A"].read_bytes(),
                                             new_document_title="EBC HLD (synthetic)", revision_label="A")
    rev_b = document_service.upload_revision(settings, sample_pdfs["B"].name, sample_pdfs["B"].read_bytes(),
                                             document_id=rev_a["document_id"], revision_label="B")
    stats_a = document_service.process(settings, rev_a["id"])
    stats_b = document_service.process(settings, rev_b["id"])
    return {"A": rev_a, "B": rev_b, "stats_A": stats_a, "stats_B": stats_b}


class FakeProvider:
    """Deterministic stand-in for an LLM provider (no network)."""

    name = "fake"
    model = "fake-model"
    is_external = False
    destination = "test"

    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def generate(self, system, user, max_tokens=None, temperature=None):
        from src.ai.llm_provider import LLMResult

        self.calls.append((system, user))
        if self.error:
            raise self.error
        return LLMResult(text=self.reply or "", provider=self.name, model=self.model, latency_ms=1)
