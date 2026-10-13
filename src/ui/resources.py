"""Cached heavy resources (embedding model, vector store, retriever) that must
survive Streamlit reruns."""

from __future__ import annotations

import streamlit as st

from config.settings import Settings
from src.ai.rag_pipeline import RAGPipeline
from src.ai.retrieval import Retriever


def _fingerprint(settings: Settings) -> tuple:
    return (settings.embedding_backend, settings.embedding_model, str(settings.chroma_dir), settings.retrieval_top_k,
            settings.hybrid_keyword_weight)


@st.cache_resource(show_spinner=False, max_entries=4)
def _retriever(_settings: Settings, key: tuple) -> Retriever:
    return Retriever(_settings)


def get_pipeline(settings: Settings) -> RAGPipeline:
    """A RAG pipeline sharing a cached retriever; the LLM provider is created per call so that
    configuration changes take effect immediately."""
    retriever = _retriever(settings, _fingerprint(settings))
    retriever.settings = settings
    return RAGPipeline(settings, retriever=retriever)
