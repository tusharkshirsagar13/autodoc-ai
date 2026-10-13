"""Retrieval-Augmented Generation pipeline with citation validation.

Answer modes
* answered               — LLM answer grounded in cited sources
* answered_uncited       — LLM answered but cited no valid source (flagged)
* insufficient_evidence  — retrieval found nothing relevant enough, or the
                           model reported that the sources are insufficient
* retrieval_only         — no LLM configured; evidence passages are returned
* provider_error         — LLM call failed; evidence passages are returned
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from config.settings import Settings
from src.ai.llm_provider import LLMProvider, get_llm_provider
from src.ai.prompts import INSUFFICIENT_MARKER, QA_SYSTEM_PROMPT, build_qa_prompt
from src.ai.retrieval import RetrievedChunk, Retriever
from src.utils.errors import LLMNotConfiguredError, LLMProviderError

_CITATION = re.compile(r"\[S(\d+)\]")


@dataclass
class Citation:
    label: str
    page_number: int
    section: str
    excerpt: str
    revision_id: str
    revision_label: str
    document_name: str
    score: float
    chunk_id: str
    cited_by_answer: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RAGAnswer:
    question: str
    answer: str
    mode: str
    citations: list[Citation] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    notice: str = ""

    @property
    def cited(self) -> list[Citation]:
        return [c for c in self.citations if c.cited_by_answer]


def _to_citations(chunks: list[RetrievedChunk]) -> list[Citation]:
    out = []
    for i, c in enumerate(chunks, start=1):
        m = c.metadata
        out.append(Citation(label=f"S{i}", page_number=int(m.get("page_number", 0)), section=m.get("section_heading", ""),
                            excerpt=c.text, revision_id=m.get("revision_id", ""), revision_label=m.get("revision_label", ""),
                            document_name=m.get("document_name", ""), score=round(c.score, 3), chunk_id=c.chunk_id))
    return out


def _fit_context(chunks: list[RetrievedChunk], max_chars: int) -> list[RetrievedChunk]:
    selected, total = [], 0
    for c in chunks:
        if selected and total + len(c.text) > max_chars:
            break
        selected.append(c)
        total += len(c.text)
    return selected


class RAGPipeline:
    def __init__(self, settings: Settings, retriever: Retriever | None = None, provider: LLMProvider | None = None):
        self.settings = settings
        self.retriever = retriever or Retriever(settings)
        self._provider = provider

    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider(self.settings)
        return self._provider

    def retrieve(self, question: str, revision_ids: list[str], top_k: int | None = None) -> list[RetrievedChunk]:
        return self.retriever.search(question, revision_ids, top_k=top_k)

    def answer(self, question: str, revision_ids: list[str], history: list[dict] | None = None,
               top_k: int | None = None) -> RAGAnswer:
        question = (question or "").strip()
        if not question:
            return RAGAnswer(question="", answer="Please enter a question.", mode="insufficient_evidence")
        if len(question) > 2000:
            question = question[:2000]
        chunks = self.retrieve(question, revision_ids, top_k=top_k)
        relevant = [c for c in chunks if c.score >= self.settings.relevance_threshold]
        if not relevant:
            return RAGAnswer(
                question=question, mode="insufficient_evidence", citations=_to_citations(chunks[:3]),
                answer=("The indexed document does not appear to contain enough relevant evidence to answer this "
                        "question. The closest passages are shown below for manual inspection; they scored below "
                        "the relevance threshold."),
            )
        context = _fit_context(relevant, self.settings.max_context_chars)
        citations = _to_citations(context)

        try:
            provider = self.provider()
        except LLMNotConfiguredError as exc:
            return RAGAnswer(question=question, mode="retrieval_only", citations=citations,
                             answer=("No AI answer was generated because no language model is configured. "
                                     "The most relevant passages from the document are listed below."),
                             notice=exc.user_message())

        prompt = build_qa_prompt(question, context, history)
        start = time.perf_counter()
        try:
            result = provider.generate(QA_SYSTEM_PROMPT, prompt)
        except LLMProviderError as exc:
            return RAGAnswer(question=question, mode="provider_error", citations=citations,
                             answer="The language model could not produce an answer. Retrieved evidence is shown below.",
                             notice=exc.user_message(), provider=provider.name, model=provider.model)
        latency = int((time.perf_counter() - start) * 1000)
        text = result.text
        if text.strip().startswith(INSUFFICIENT_MARKER):
            detail = text.strip()[len(INSUFFICIENT_MARKER):].strip(" :.-\n")
            return RAGAnswer(question=question, mode="insufficient_evidence", citations=citations,
                             answer="The retrieved sources do not contain enough evidence to answer this question."
                                    + (f" {detail}" if detail else ""),
                             provider=result.provider, model=result.model, latency_ms=latency)

        valid_labels = {c.label for c in citations}
        used = {f"S{n}" for n in _CITATION.findall(text)}
        invalid = used - valid_labels
        if invalid:
            text = _CITATION.sub(lambda m: m.group(0) if f"S{m.group(1)}" in valid_labels else "[citation removed]", text)
        for c in citations:
            c.cited_by_answer = c.label in used
        mode = "answered" if used & valid_labels else "answered_uncited"
        notice = ""
        if mode == "answered_uncited":
            notice = "The model did not cite any source for this answer. Treat it as unverified and check the evidence."
        elif invalid:
            notice = "The model referenced sources that were not supplied; those references were removed."
        return RAGAnswer(question=question, answer=text, mode=mode, citations=citations, provider=result.provider,
                         model=result.model, latency_ms=latency, notice=notice)
